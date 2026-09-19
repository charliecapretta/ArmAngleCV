"""Production entry point: one Gunicorn worker, SQLite, private disk storage."""
from contextlib import closing, contextmanager
from datetime import timedelta
import fcntl
import hashlib
import hmac
import ipaddress
import json
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
from urllib.parse import urlsplit

from flask import Flask, Response, abort, g, jsonify, redirect, render_template, request, session
from werkzeug.exceptions import HTTPException
from werkzeug.security import check_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix

import analysisExport
import angleInputs
import labelServer
from hosted.video import MAX_BYTES, MAX_FRAME_BYTES
from hosted import example, storage

ROOT = Path(__file__).resolve().parents[1]


class CloudFrontVpcProxy:
    """Only for origins restricted to the CloudFront VPC service security group.

    CloudFront generates these headers; nginx's HTTP scheme and the viewer's
    arbitrary X-Forwarded-* headers must not override them. Gunicorn stays on
    loopback. AllViewerAndCloudFrontHeaders-2022-06 must forward these headers.
    """
    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        if environ.get('PATH_INFO') != '/healthz':
            try:
                if environ.get('HTTP_CLOUDFRONT_FORWARDED_PROTO') != 'https':
                    raise ValueError('HTTPS viewer required')
                address, separator, port = environ.get('HTTP_CLOUDFRONT_VIEWER_ADDRESS', '').rpartition(':')
                if not separator or not port.isdigit() or not 0 < int(port) <= 65535:
                    raise ValueError('Invalid viewer address')
                viewer = str(ipaddress.ip_address(address.strip('[]')))
            except ValueError:
                response = Response('{"error":"CloudFront HTTPS forwarding is not configured correctly."}', status=403,
                                    mimetype='application/json', headers={'Cache-Control':'no-store'})
                return response(environ, start_response)
            environ['wsgi.url_scheme'] = 'https'
            environ['REMOTE_ADDR'] = viewer
        return self.app(environ, start_response)


SCHEMA = '''
CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS videos(id TEXT PRIMARY KEY, owner TEXT NOT NULL REFERENCES users(id), metadata TEXT NOT NULL, created REAL NOT NULL, bytes INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS pitches(id TEXT PRIMARY KEY, video TEXT NOT NULL REFERENCES videos(id) ON DELETE CASCADE, metadata TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS labels(pitch TEXT PRIMARY KEY REFERENCES pitches(id) ON DELETE CASCADE, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS undo(token TEXT PRIMARY KEY, owner TEXT NOT NULL, pitch TEXT NOT NULL REFERENCES pitches(id) ON DELETE CASCADE, previous TEXT, expected TEXT, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS attempts(key TEXT PRIMARY KEY, count INTEGER NOT NULL, since REAL NOT NULL);
CREATE INDEX IF NOT EXISTS videos_owner ON videos(owner);
CREATE TABLE IF NOT EXISTS s3_objects(video TEXT PRIMARY KEY, bucket TEXT NOT NULL, key TEXT NOT NULL, region TEXT NOT NULL, version TEXT, state TEXT NOT NULL);
'''


def connect(root):
    db = sqlite3.connect(str(Path(root) / 'app.sqlite3'), timeout=10)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    return db


def initialize(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    (root / 'videos').mkdir(exist_ok=True, mode=0o700)
    with closing(connect(root)) as db, db:
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript(SCHEMA)


@contextmanager
def media_lock(root):
    with (Path(root) / 'media.lock').open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            abort(429, description='Another video is being prepared. Please retry in a minute.')
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def cleanup(root, retention=7 * 86400, drain=True):
    """Caller holds media.lock; orphan folders also cover interrupted uploads."""
    with closing(connect(root)) as db, db:
        db.execute("UPDATE s3_objects SET state='delete' WHERE state='pending' OR video IN (SELECT id FROM videos WHERE created < ?)", (time.time() - retention,))
        db.execute('DELETE FROM videos WHERE created < ?', (time.time() - retention,))
        db.execute('DELETE FROM undo WHERE created < ?', (time.time() - 86400,))
        db.execute('DELETE FROM attempts WHERE since < ?', (time.time() - 3600,))
        db.execute("DELETE FROM users WHERE password='!' AND username LIKE 'guest:%' AND id NOT IN (SELECT owner FROM videos)")
        active = {r['id'] for r in db.execute('SELECT id FROM videos')}
    for path in (Path(root) / 'videos').iterdir():
        if path.is_dir() and path.name not in active:
            shutil.rmtree(path)
    if drain:
        storage.drain_deletions(root)


def create_app(config=None):
    app = Flask(__name__, static_folder=None)
    app.config.update(
        SECRET_KEY=os.environ.get('ARMANGLE_SECRET'),
        DATA_DIR=os.environ.get('ARMANGLE_DATA', '/var/lib/armangle'),
        PUBLIC_ORIGIN=os.environ.get('ARMANGLE_ORIGIN', ''),
        ACCESS_MODE=os.environ.get('ARMANGLE_ACCESS', 'accounts'),
        PROXY_MODE=os.environ.get('ARMANGLE_PROXY', 'single'),
        S3_BUCKET=os.environ.get('ARMANGLE_S3_BUCKET', ''),
        S3_REGION=os.environ.get('ARMANGLE_S3_REGION', ''),
        MAX_CONTENT_LENGTH=MAX_BYTES,
        SESSION_COOKIE_NAME='armangle_session', SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=True, SESSION_COOKIE_SAMESITE='Lax',
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
        USER_QUOTA=500 * 1024 * 1024, TOTAL_QUOTA=5 * 1024**3,
        RETENTION=7 * 86400,
    )
    if config:
        app.config.update(config)
    storage.validate_config(app.config['S3_BUCKET'], app.config['S3_REGION'])
    if app.config['ACCESS_MODE'] not in ('accounts', 'anonymous'):
        raise RuntimeError('ARMANGLE_ACCESS must be accounts or anonymous.')
    if app.config['PROXY_MODE'] not in ('single', 'cloudfront-vpc'):
        raise RuntimeError('ARMANGLE_PROXY must be single or cloudfront-vpc.')
    anonymous = app.config['ACCESS_MODE'] == 'anonymous'
    if anonymous:
        app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)
    if not app.config['SECRET_KEY'] or len(app.config['SECRET_KEY']) < 32 or str(app.config['SECRET_KEY']).startswith('REPLACE_'):
        raise RuntimeError('ARMANGLE_SECRET must contain at least 32 random characters.')
    origin = app.config['PUBLIC_ORIGIN'].rstrip('/')
    parsed_origin = urlsplit(origin)
    if not app.testing and (parsed_origin.scheme != 'https' or not parsed_origin.hostname or
                           parsed_origin.path or parsed_origin.query or parsed_origin.fragment or
                           parsed_origin.username or parsed_origin.password):
        raise RuntimeError('ARMANGLE_ORIGIN must be the public https:// address.')
    manifest = json.loads((ROOT / 'model-manifest.json').read_text())
    model_path = ROOT / angleInputs.DEFAULT_MODEL
    if hashlib.sha256(model_path.read_bytes()).hexdigest() != manifest['sha256'][model_path.name]:
        raise RuntimeError('The hosted model checksum does not match the release manifest.')
    bundle = labelServer.getManualAngleBundle()
    if bundle.get('features') != angleInputs.columns(bundle.get('feature_mode')):
        raise RuntimeError('The hosted model feature schema is invalid.')
    root = Path(app.config['DATA_DIR'])
    initialize(root)
    # Only deploy behind a trusted loopback proxy with verified forwarding headers.
    if app.config['PROXY_MODE'] == 'cloudfront-vpc':
        app.wsgi_app = CloudFrontVpcProxy(app.wsgi_app)
    else:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

    def db():
        if 'db' not in g:
            g.db = connect(root)
        return g.db

    @app.teardown_appcontext
    def close_db(_error):
        if 'db' in g:
            g.db.close()

    def csrf():
        if 'csrf' not in session:
            session['csrf'] = secrets.token_hex(32)
        return session['csrf']

    @app.before_request
    def protect():
        if request.path == '/healthz':
            return
        if not app.testing and request.host_url.rstrip('/') != origin:
            abort(400, description='Unexpected host.')
        if request.method == 'POST':
            if request.path != '/api/upload' and (request.content_length is None or request.content_length > 65536):
                abort(413)
            supplied = request.headers.get('X-CSRF-Token') or (request.form.get('csrf') if request.path in ('/login', '/logout') else '')
            if not supplied or not hmac.compare_digest(str(supplied), session.get('csrf', '')):
                abort(403, description='Your session expired. Reload the analysis page and retry.')
            if request.headers.get('Origin') and request.headers['Origin'] != origin:
                abort(403, description='Unexpected request origin.')
        if request.path not in ('/login', '/workspace.css'):
            if anonymous:
                identifier = session.get('guest_id')
                if identifier is None:
                    # Only an entry page may start a session. Assets, health checks
                    # and arbitrary API requests cannot create visitor records.
                    if request.method == 'GET' and request.path in ('/', '/index.html', '/analyze.html', '/account'):
                        session.clear()
                        identifier = secrets.token_hex(32)
                        session.update(guest_id=identifier, csrf=secrets.token_hex(32))
                        session.permanent = True
                    elif request.path.startswith('/api/'):
                        abort(401, description='Open the analysis page to start a browser session.')
                    else:
                        return redirect('/analyze.html')
                g.user = dict(id='guest:' + identifier, username='Guest')
                return
            user = db().execute('SELECT * FROM users WHERE id=? AND version=?',
                                (session.get('uid'), session.get('version'))).fetchone()
            if user is None:
                if request.path.startswith('/api/'):
                    abort(401, description='Please sign in again at /login.')
                return redirect('/login')
            g.user = user

    @app.after_request
    def headers(response):
        response.headers['Cache-Control'] = 'no-store, private'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Content-Security-Policy'] = "default-src 'self'; img-src 'self' blob: data:; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        return response

    @app.errorhandler(Exception)
    def error(error):
        if isinstance(error, HTTPException):
            code, message = error.code, error.description
        elif isinstance(error, (ValueError, TypeError, KeyError, OverflowError)):
            code, message = 400, 'Invalid request. Check the selected pitch and input values.'
        else:
            app.logger.exception('Hosted request failed')
            code, message = 500, 'The request could not be completed. Please retry.'
        return jsonify(error=message), code

    @app.get('/healthz')
    def health():
        db().execute('SELECT 1 FROM users LIMIT 1').fetchone()
        return {'ok': True}

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        if anonymous:
            return redirect('/analyze.html')
        error = None
        if request.method == 'POST':
            username = request.form.get('username', '').strip().lower()[:100]
            # Both account and client address limits are persisted across restarts.
            keys = ['ip:' + str(request.remote_addr), 'user:' + username]
            now = time.time()
            with db():
                for key in keys:
                    row = db().execute('SELECT * FROM attempts WHERE key=?', (key,)).fetchone()
                    if row and now - row['since'] < 900 and row['count'] >= 10:
                        abort(429, description='Too many sign-in attempts. Wait 15 minutes.')
                    db().execute('INSERT INTO attempts VALUES (?,1,?) ON CONFLICT(key) DO UPDATE SET count=CASE WHEN ?-since>=900 THEN 1 ELSE count+1 END, since=CASE WHEN ?-since>=900 THEN ? ELSE since END', (key, now, now, now, now))
            user = db().execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
            if user and check_password_hash(user['password'], request.form.get('password', '')[:256]):
                # Reserve attempts before hashing, but do not penalize successful
                # testers sharing one public IP or reopening their accounts.
                with db():
                    for key in keys:
                        db().execute('UPDATE attempts SET count=MAX(0,count-1) WHERE key=?', (key,))
                session.clear()
                session.update(uid=user['id'], version=user['version'], csrf=secrets.token_hex(32))
                session.permanent = True
                return redirect('/analyze.html')
            error = 'Incorrect username or password.'
        return render_template('login.html', csrf=csrf(), error=error)

    @app.post('/logout')
    def logout():
        session.clear()
        return redirect('/login')

    @app.get('/')
    @app.get('/index.html')
    @app.get('/analyze.html')
    def index():
        hosted_attributes = 'data-workspace="analysis" data-hosted="true"'
        if example.available(root):
            hosted_attributes += ' data-example="true"'
        html = (ROOT / 'labelTool.html').read_text().replace('data-workspace="training"', hosted_attributes)
        html = html.replace('</head>', '<meta name="csrf-token" content="' + csrf() + '"></head>')
        html = html.replace('</header>', '<a href="/account">' + ('My uploads' if anonymous else 'My account') + '</a></header>')
        html = html.replace('MP4 · up to 100 MB, 60 seconds, 1920 × 1080, 60 fps, and 3,600 frames. A copy is saved by this app.',
                            'Private beta · MP4 up to 50 MB, 30 seconds, 1920 × 1080, 60 fps. Videos and annotations expire after 7 days. Export your CSV to keep results. Storage limit: 500 MB per account, including prepared frames. One upload is prepared at a time.')
        if anonymous:
            html = html.replace('Private beta · MP4 up to 50 MB, 30 seconds, 1920 × 1080, 60 fps.',
                                'Use 60fps 1080 for best results<br>MP4 up to 50 MB and 30 seconds.').replace('500 MB per account', '500 MB per browser')
            html = html.replace('One upload is prepared at a time.', 'One upload is prepared at a time. Your uploads are available only in this browser. Clearing cookies or using another device starts a new workspace. Export your CSV to keep your results.')
        return Response(html, mimetype='text/html')

    @app.get('/workspace.css')
    @app.get('/labelTool.js')
    def asset():
        name = request.path[1:]
        return Response((ROOT / name).read_bytes(), mimetype='text/css' if name.endswith('.css') else 'text/javascript')

    def owned_pitch(identifier):
        row = db().execute('SELECT p.*,v.metadata AS video_meta,v.created FROM pitches p JOIN videos v ON v.id=p.video WHERE p.id=? AND v.owner=? AND v.created>=?',
                           (identifier, g.user['id'], time.time() - app.config['RETENTION'])).fetchone()
        if row is None:
            abort(404, description='Pitch not found or expired.')
        pitch = json.loads(row['metadata'])
        meta = json.loads(row['video_meta'])
        return row, pitch, meta

    def payload():
        if request.content_length is None or request.content_length > 65536:
            abort(413)
        value = request.get_json()
        if not isinstance(value, dict):
            abort(400)
        if value.get('workspace', 'analysis') != 'analysis':
            abort(403)
        return value

    def validate(value):
        if value.get('game_pk') != 'local':
            abort(404)
        row, pitch, meta = owned_pitch(value.get('play_id'))
        entry = dict(meta, jpegs=range(meta['nFrames']))
        # An example carries Statcast truth. Take it from the pitch, so a browser
        # cannot rewrite the reference its own annotation is compared against.
        value = dict(value, workspace='analysis', park=pitch.get('park') or '',
                     true_arm_angle='' if pitch.get('armAngle') is None else pitch['armAngle'])
        clean = labelServer.validateLabel(value, entry=entry, pitch=pitch)
        return {key: clean.get(key, '') for key in labelServer.LABEL_COLUMNS}

    def list_pitches():
        return [json.loads(r['metadata']) for r in db().execute('SELECT p.metadata FROM pitches p JOIN videos v ON p.video=v.id WHERE v.owner=? AND v.created>=? ORDER BY v.created,p.id', (g.user['id'], time.time() - app.config['RETENTION']))]

    def list_labels():
        return [json.loads(r['body']) for r in db().execute('SELECT l.body FROM labels l JOIN pitches p ON l.pitch=p.id JOIN videos v ON p.video=v.id WHERE v.owner=? AND v.created>=?', (g.user['id'], time.time() - app.config['RETENTION']))]

    @app.get('/api/pitches')
    def pitches():
        return {'pitches': list_pitches()}

    @app.get('/api/labels')
    def labels():
        return {'labels': list_labels()}

    @app.get('/api/clip')
    @app.get('/api/frame')
    def clip():
        directory = request.args.get('dir', '')
        if not directory.startswith('local_'):
            abort(404)
        row, pitch, meta = owned_pitch(directory[6:])
        if request.path == '/api/clip':
            return {key: meta[key] for key in ('nFrames', 'fps', 'frameTimes')}
        index = int(request.args.get('frame', '0'))
        if not 0 <= index < meta['nFrames']:
            abort(404)
        path = root / 'videos' / row['video'] / f'{index:04d}.jpg'
        if not path.is_file():
            abort(404)
        return Response(path.read_bytes(), mimetype='image/jpeg')

    @app.post('/api/predict-angle')
    def predict():
        row = validate(payload())
        if row['status'] != 'labeled':
            abort(400, description='Five visible points are required.')
        bundle = labelServer.getManualAngleBundle()
        value = float(angleInputs.predict(bundle, [row])[0])
        return dict(ok=True, predicted_arm_angle=value, feature_mode=bundle['feature_mode'], model_version=analysisExport.MODEL_VERSION, note=analysisExport.ESTIMATE_SCOPE)

    @app.post('/api/label')
    def save():
        row = validate(payload())
        body = json.dumps(row, sort_keys=True)
        token = uuid.uuid4().hex
        with db():
            db().execute('BEGIN IMMEDIATE')
            previous = db().execute('SELECT body FROM labels WHERE pitch=?', (row['play_id'],)).fetchone()
            db().execute('INSERT INTO labels VALUES (?,?) ON CONFLICT(pitch) DO UPDATE SET body=excluded.body', (row['play_id'], body))
            db().execute('INSERT INTO undo VALUES (?,?,?,?,?,?)', (token, g.user['id'], row['play_id'], previous['body'] if previous else None, body, time.time()) )
            db().execute('DELETE FROM undo WHERE owner=? AND token NOT IN (SELECT token FROM undo WHERE owner=? ORDER BY created DESC LIMIT 100)', (g.user['id'], g.user['id']))
        return dict(ok=True, total=len(list_labels()), undoToken=token)

    @app.post('/api/undo')
    def undo():
        value = payload()
        with db():
            db().execute('BEGIN IMMEDIATE')
            undo = db().execute('SELECT * FROM undo WHERE token=? AND owner=? AND created>=?', (value.get('undoToken'), g.user['id'], time.time() - 86400)).fetchone()
            if not undo:
                abort(404, description='Undo is no longer available.')
            owned_pitch(undo['pitch'])
            current = db().execute('SELECT body FROM labels WHERE pitch=?', (undo['pitch'],)).fetchone()
            if not current or current['body'] != undo['expected']:
                abort(409, description='This pitch changed after the save. Reopen it before editing.')
            if undo['previous'] is None:
                db().execute('DELETE FROM labels WHERE pitch=?', (undo['pitch'],))
            else:
                db().execute('UPDATE labels SET body=? WHERE pitch=?', (undo['previous'], undo['pitch']))
            db().execute('DELETE FROM undo WHERE token=?', (undo['token'],))
        return dict(ok=True, total=len(list_labels()))

    @app.post('/api/delete-label')
    def delete_label():
        value = payload()
        owned_pitch(value.get('play_id'))
        with db():
            db().execute('DELETE FROM labels WHERE pitch=?', (value['play_id'],))
            db().execute('DELETE FROM undo WHERE pitch=?', (value['play_id'],))
        return dict(ok=True, total=len(list_labels()))

    def new_pitch(video, meta, details):
        identifier = uuid.uuid4().hex
        pitch = dict(gamePk='local', playId=identifier, dirName='local_' + identifier,
                     sessionId=video, sessionName=details['sessionName'], sourceName=details['sourceName'],
                     pitcherName=details['pitcherName'], pThrows=details['pThrows'], park='',
                     armAngle=None, startFrame=0, isLocal=True,
                     **{key: meta[key] for key in ('nFrames', 'fps', 'imgW', 'imgH')})
        db().execute('INSERT INTO pitches VALUES (?,?,?)', (identifier, video, json.dumps(pitch)))
        return pitch

    @app.post('/api/import')
    def add_pitch():
        value = payload()
        if set(value) - {'parentId', 'workspace', 'startFrame'} or not value.get('parentId'):
            abort(400, description='Only adding a pitch to your own uploaded session is supported.')
        row, parent, meta = owned_pitch(value['parentId'])
        start = int(value.get('startFrame', 0))
        if float(value.get('startFrame', 0)) != start or not 0 <= start < meta['nFrames']:
            abort(400)
        with db():
            db().execute('BEGIN IMMEDIATE')
            if db().execute('SELECT COUNT(*) FROM pitches WHERE video=?', (row['video'],)).fetchone()[0] >= 50:
                abort(400, description='Maximum 50 pitches per session.')
            pitch = new_pitch(row['video'], meta, parent)
            pitch['startFrame'] = start
            db().execute('UPDATE pitches SET metadata=? WHERE id=?', (json.dumps(pitch), pitch['playId']))
        return dict(ok=True, pitch=pitch)

    @app.post('/api/example')
    def add_example():
        """Hand this visitor their own copy of the shared practice clip."""
        value = payload()
        if set(value) - {'workspace'}:
            abort(400)
        slugs = example.available(root)
        if not slugs:
            abort(404, description='No practice clip is installed on this server.')
        slug = slugs[0]
        mine = example.existing(list_pitches(), slug)
        if mine is not None:
            return dict(ok=True, pitch=mine, reused=True)
        with media_lock(root):
            if shutil.disk_usage(root).free < 1024 ** 3:
                abort(413, description='Storage is full. Please try again later.')
            with db():
                db().execute('BEGIN IMMEDIATE')
                pitch = example.clone(db(), root, g.user['id'], slug, guest=anonymous)
        return dict(ok=True, pitch=pitch, reused=False)

    @app.get('/api/export')
    def export():
        result = analysisExport.exportCsv(list_labels(), list_pitches(), labelServer.getManualAngleBundle(), request.args.get('session'))
        return Response(result.encode('utf-8-sig'), mimetype='text/csv', headers={'Content-Disposition': 'attachment; filename="arm-angle-session.csv"'})

    @app.post('/api/upload')
    def upload():
        if not request.content_length or not 0 < request.content_length <= MAX_BYTES:
            abort(413, description='Choose an MP4 up to 50 MB.')
        name = request.args.get('filename', '').replace('\\', '/').split('/')[-1]
        if not name.lower().endswith('.mp4') or len(name) > 240:
            abort(400, description='Choose an MP4 video.')
        throws = request.args.get('pThrows', 'R')
        if throws not in ('R', 'L'):
            abort(400)
        with media_lock(root):
            cleanup(root, app.config['RETENTION'], drain=False)
            if anonymous:
                # A new cookie must not bypass the per-address upload budget.
                # Store only an HMAC of the address, not the address itself.
                address = hmac.new(str(app.config['SECRET_KEY']).encode(), str(request.remote_addr).encode(), hashlib.sha256).hexdigest()
                budgets = [('upload-ip:' + address, 30), ('upload-guest:' + g.user['id'], 10)]
                now = time.time()
                with db():
                    db().execute('BEGIN IMMEDIATE')
                    for key, limit in budgets:
                        row = db().execute('SELECT * FROM attempts WHERE key=?', (key,)).fetchone()
                        if row and now - row['since'] < 3600 and row['count'] >= limit:
                            abort(429, description='Upload limit reached. Please try again in an hour.')
                    for key, _ in budgets:
                        db().execute('INSERT INTO attempts VALUES (?,1,?) ON CONFLICT(key) DO UPDATE SET count=count+1', (key, now))
            used = db().execute('SELECT COALESCE(SUM(bytes),0) FROM videos WHERE owner=?', (g.user['id'],)).fetchone()[0]
            total = db().execute('SELECT COALESCE(SUM(bytes),0) FROM videos').fetchone()[0]
            reserve = MAX_BYTES + MAX_FRAME_BYTES
            if used + reserve > app.config['USER_QUOTA'] or total + reserve > app.config['TOTAL_QUOTA'] or shutil.disk_usage(root).free < reserve + 1024**3:
                abort(413, description='Storage is full. Delete an older session from ' + ('My uploads' if anonymous else 'My account') + ' and retry.')
            video = uuid.uuid4().hex
            directory = root / 'videos' / video
            directory.mkdir(mode=0o700)
            registered = False
            try:
                with (directory / 'source.mp4').open('xb') as handle:
                    remaining = request.content_length
                    while remaining:
                        chunk = request.stream.read(min(1024 * 1024, remaining))
                        if not chunk:
                            abort(400, description='Upload was interrupted.')
                        handle.write(chunk)
                        remaining -= len(chunk)
                result = subprocess.run([sys.executable, '-m', 'hosted.video', str(directory)], cwd=ROOT,
                                        capture_output=True, text=True, timeout=100,
                                        env={**os.environ, 'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1'})
                if result.returncode:
                    abort(400, description=result.stderr.strip()[-300:] or 'Video preparation failed.')
                meta = json.loads((directory / 'meta.json').read_text())
                size = sum(p.stat().st_size for p in directory.iterdir())
                details = dict(sourceName=name, sessionName=request.args.get('sessionName', '')[:160] or name,
                               pitcherName=request.args.get('pitcherName', '')[:160], pThrows=throws)
                if app.config['S3_BUCKET']:
                    try:
                        meta['original'] = storage.store_original(root, video, directory / 'source.mp4',
                                                                  app.config['S3_BUCKET'], app.config['S3_REGION'])
                        (directory / 'meta.json').write_text(json.dumps(meta))
                    except Exception:
                        app.logger.exception('S3 original storage failed')
                        abort(503, description='Video storage is temporarily unavailable. Please retry later.')
                with db():
                    if anonymous:
                        db().execute("INSERT OR IGNORE INTO users(id,username,password) VALUES (?,?,'!')", (g.user['id'], g.user['id']))
                    db().execute('INSERT INTO videos VALUES (?,?,?,?,?)', (video, g.user['id'], json.dumps(meta), time.time(), size))
                    pitch = new_pitch(video, meta, details)
                    db().execute("UPDATE s3_objects SET state='active' WHERE video=?", (video,))
                registered = True
                if app.config['S3_BUCKET']:
                    # Registration and S3 succeeded. Frames remain local for annotation.
                    try:
                        (directory / 'source.mp4').unlink()
                    except OSError:
                        app.logger.warning('Could not remove staged original for %s', video)
                return dict(ok=True, pitch=pitch)
            except subprocess.TimeoutExpired:
                abort(400, description='Video preparation timed out. Try a shorter clip.')
            finally:
                if not registered:
                    shutil.rmtree(directory, ignore_errors=True)
                    with db():
                        db().execute("UPDATE s3_objects SET state='delete' WHERE video=?", (video,))

    @app.get('/account')
    def account():
        videos = db().execute('SELECT v.id,v.created,MIN(p.metadata) AS metadata FROM videos v JOIN pitches p ON p.video=v.id WHERE v.owner=? GROUP BY v.id ORDER BY v.created DESC', (g.user['id'],)).fetchall()
        items = [dict(id=v['id'], name=json.loads(v['metadata'])['sessionName'], expires=time.strftime('%Y-%m-%d', time.gmtime(v['created'] + app.config['RETENTION']))) for v in videos]
        return render_template('account.html', username=g.user['username'], videos=items, csrf=csrf(), anonymous=anonymous)

    @app.post('/api/delete-session')
    def delete_session():
        value = payload()
        with media_lock(root):
            row = db().execute('SELECT id FROM videos WHERE id=? AND owner=?', (value.get('sessionId'), g.user['id'])).fetchone()
            if not row:
                abort(404)
            with db():
                db().execute("UPDATE s3_objects SET state='delete' WHERE video=?", (row['id'],))
                db().execute('DELETE FROM videos WHERE id=?', (row['id'],))
            shutil.rmtree(root / 'videos' / row['id'], ignore_errors=True)
            pending = storage.drain_deletions(root, video=row['id'])
        return dict(ok=True, storageDeletionPending=bool(pending))

    @app.get('/account.js')
    def account_js():
        return Response((ROOT / 'hosted/account.js').read_text(), mimetype='text/javascript')

    return app
