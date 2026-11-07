"""Production entry point: one Gunicorn worker, SQLite, private disk storage."""
from contextlib import closing, contextmanager
from datetime import timedelta
import fcntl
import hashlib
import hmac
import ipaddress
import json
import os
import re
from pathlib import Path
import secrets
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
from urllib.parse import urlsplit

from flask import Flask, Response, abort, g, jsonify, redirect, render_template, request, session, send_file
from flask.sessions import SecureCookieSessionInterface
from werkzeug.exceptions import HTTPException, TooManyRequests, RequestEntityTooLarge
from werkzeug.security import check_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix

from armangle import analysisExport
from armangle import angleInputs
from armangle import labelServer
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
CREATE TABLE IF NOT EXISTS dailyBudgets(key TEXT PRIMARY KEY, count INTEGER NOT NULL, resetAt INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS videos_owner ON videos(owner);
CREATE TABLE IF NOT EXISTS s3_objects(video TEXT PRIMARY KEY, bucket TEXT NOT NULL, key TEXT NOT NULL, region TEXT NOT NULL, version TEXT, state TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS deletions(video TEXT PRIMARY KEY, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
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
        # Finish a deletion interrupted after its ledger commit.
        db.execute("UPDATE s3_objects SET state='delete' WHERE video IN (SELECT video FROM deletions)")
        db.execute('DELETE FROM videos WHERE id IN (SELECT video FROM deletions)')
        db.execute("UPDATE s3_objects SET state='delete' WHERE state='pending' OR video IN (SELECT id FROM videos WHERE created < ?)", (time.time() - retention,))
        db.execute('DELETE FROM videos WHERE created < ?', (time.time() - retention,))
        db.execute('DELETE FROM undo WHERE created < ?', (time.time() - 86400,))
        db.execute('DELETE FROM attempts WHERE since < ?', (time.time() - 3600,))
        db.execute('DELETE FROM dailyBudgets WHERE resetAt <= ?', (time.time(),))
        db.execute("DELETE FROM users WHERE password='!' AND username LIKE 'guest:%' AND id NOT IN (SELECT owner FROM videos)")
        active = {r['id'] for r in db.execute('SELECT id FROM videos')}
    for path in (Path(root) / 'videos').iterdir():
        if path.is_dir() and path.name not in active:
            shutil.rmtree(path)
    if drain:
        return storage.drain_deletions(root)
    return 0


class publicAssetSessions(SecureCookieSessionInterface):
    def save_session(self, app, session, response):
        # Publicly cached assets must never carry a visitor's Set-Cookie header,
        # including Flask's automatic refresh of an existing permanent session.
        if request.endpoint == 'asset':
            return
        super().save_session(app, session, response)


def create_app(config=None):
    app = Flask(__name__, static_folder=None)
    app.session_interface = publicAssetSessions()
    app.config.update(
        SECRET_KEY=os.environ.get('ARMANGLE_SECRET'),
        DATA_DIR=os.environ.get('ARMANGLE_DATA', '/var/lib/armangle'),
        PUBLIC_ORIGIN=os.environ.get('ARMANGLE_ORIGIN', ''),
        ACCESS_MODE=os.environ.get('ARMANGLE_ACCESS', 'accounts'),
        PROXY_MODE=os.environ.get('ARMANGLE_PROXY', 'single'),
        S3_BUCKET=os.environ.get('ARMANGLE_S3_BUCKET', ''),
        S3_REGION=os.environ.get('ARMANGLE_S3_REGION', ''),
        SUPPORT_EMAIL=os.environ.get('ARMANGLE_SUPPORT_EMAIL', ''),
        DELETION_BUCKET=os.environ.get('ARMANGLE_DELETION_BUCKET', ''),
        DELETION_REGION=os.environ.get('ARMANGLE_DELETION_REGION', ''),
        RETAIN_ORIGINALS=os.environ.get('ARMANGLE_RETAIN_ORIGINALS', 'off') == 'on',
        PUBLIC_RELEASE=os.environ.get('ARMANGLE_PUBLIC_RELEASE', 'off') == 'on',
        EXAMPLES_ENABLED=os.environ.get('ARMANGLE_EXAMPLES', 'on') == 'on',
        MAX_CONTENT_LENGTH=MAX_BYTES + 65536,
        # Werkzeug parses file chunks in 64 KiB blocks; a lower form-memory
        # ceiling rejects valid large files before the decoder sees them.
        MAX_FORM_MEMORY_SIZE=131072, MAX_FORM_PARTS=5,
        SESSION_COOKIE_NAME='armangle_session', SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=True, SESSION_COOKIE_SAMESITE='Lax',
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
        USER_QUOTA=1024**3, TOTAL_QUOTA=5 * 1024**3,
        RETENTION=7 * 86400,
        FRAME_DAILY_LIMIT=10000, FRAME_IP_DAILY_LIMIT=20000,
        UPLOAD_DAILY_LIMIT=10, UPLOAD_IP_DAILY_LIMIT=30,
    )
    if config:
        app.config.update(config)
    if app.config['SUPPORT_EMAIL'] and not re.fullmatch(r'[A-Za-z0-9.!#$%&\'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', app.config['SUPPORT_EMAIL']):
        raise RuntimeError('ARMANGLE_SUPPORT_EMAIL must be an email address.')
    storage.validate_config(app.config['S3_BUCKET'], app.config['S3_REGION'])
    storage.validate_config(app.config['DELETION_BUCKET'], app.config['DELETION_REGION'])
    if app.config['PUBLIC_RELEASE'] and (not app.config['SUPPORT_EMAIL'] or not app.config['DELETION_BUCKET']):
        raise RuntimeError('Public release requires a support email and an off-instance deletion ledger.')
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
    manifest = json.loads((ROOT / 'model' / 'model-manifest.json').read_text())
    model_path = ROOT / angleInputs.DEFAULT_MODEL
    if hashlib.sha256(model_path.read_bytes()).hexdigest() != manifest['sha256'][angleInputs.DEFAULT_MODEL]:
        raise RuntimeError('The hosted model checksum does not match the release manifest.')
    bundle = labelServer.getManualAngleBundle()
    if bundle.get('features') != angleInputs.columns(bundle.get('feature_mode')):
        raise RuntimeError('The hosted model feature schema is invalid.')
    root = Path(app.config['DATA_DIR'])
    initialize(root)
    if (root / 'restore.pending').exists():
        raise RuntimeError('Restored data is quarantined. Run restore-reconcile before starting the service.')
    from hosted.deletions import configure
    configure(root, app.config['DELETION_BUCKET'], app.config['DELETION_REGION'])
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
        if request.endpoint == 'asset' and request.method in ('GET', 'HEAD'):
            return
        if request.method == 'POST':
            if request.path != '/api/upload' and (request.content_length is None or request.content_length > 65536):
                abort(413)
            supplied = request.headers.get('X-CSRF-Token') or (request.form.get('csrf') if request.path in ('/login', '/logout') else '')
            if not supplied or not hmac.compare_digest(str(supplied), session.get('csrf', '')):
                abort(403, description='Your session expired. Reload the analysis page and retry.')
            if request.headers.get('Origin') and request.headers['Origin'] != origin:
                abort(403, description='Unexpected request origin.')
        if request.path not in ('/login', '/workspace.css', '/privacy'):
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
        if 'Cache-Control' not in response.headers:
            response.headers['Cache-Control'] = 'no-store, private'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['X-Frame-Options'] = 'DENY'
        if request.is_secure:
            response.headers['Strict-Transport-Security'] = 'max-age=31536000'
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
        response = jsonify(error=message)
        response.status_code = code
        if code == 429:
            response.headers['Retry-After'] = str(getattr(error, 'retry_after', None) or 60)
        if code == 413 and request.path == '/api/upload' and message == RequestEntityTooLarge.description:
            response = jsonify(error='Choose an MP4 up to 200 MB.')
            response.status_code = 413
        return response

    def dailyBudget(kind, sessionLimit, addressLimit):
        # 10,000 frames permits 5 complete passes over all five 362-frame demos
        # (9,050), plus 950 requests for revisits, full-size locks and thumbnails.
        # An independent address budget survives cookie rotation. SQLite reserves
        # both atomically across threads/workers/restarts; invalid frame IDs count.
        now = int(time.time())
        resetAt = (now // 86400 + 1) * 86400  # UTC day boundary
        addressKey = hmac.new(str(app.config['SECRET_KEY']).encode(),
                              str(request.remote_addr).encode(), hashlib.sha256).hexdigest()
        sessionKey = session.get('guest_id') or session.get('csrf')
        limits = [(kind + ':session:' + str(sessionKey), sessionLimit),
                  (kind + ':ip:' + addressKey, addressLimit)]
        with db():
            db().execute('BEGIN IMMEDIATE')
            for budgetKey, limit in limits:
                row = db().execute('SELECT count,resetAt FROM dailyBudgets WHERE key=?', (budgetKey,)).fetchone()
                if row and row['resetAt'] > now and row['count'] >= limit:
                    raise TooManyRequests(description=f'Daily {kind} limit reached. Please try again after midnight UTC.',
                                          retry_after=resetAt-now)
            for budgetKey, _ in limits:
                db().execute('INSERT INTO dailyBudgets VALUES (?,1,?) ON CONFLICT(key) DO UPDATE SET '
                             'count=CASE WHEN resetAt<=? THEN 1 ELSE count+1 END, resetAt=excluded.resetAt',
                             (budgetKey, resetAt, now))

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
        example_count = min(5, len(example.available(root))) if app.config['EXAMPLES_ENABLED'] else 0
        if example_count:
            hosted_attributes += f' data-example="true" data-example-count="{example_count}"'
        html = (ROOT / 'web' / 'labelTool.html').read_text().replace('data-workspace="training"', hosted_attributes)
        html = html.replace('</head>', '<meta name="csrf-token" content="' + csrf() + '"></head>')
        html = html.replace('</header>', '<a href="/account">' + ('My uploads' if anonymous else 'My account') + '</a> <a href="/privacy">Privacy &amp; data</a></header>')
        if (root / 'uploads.paused').exists():
            html = html.replace('</header>', '</header><p role="status">New uploads and practice sessions are temporarily paused. You can still review and export saved work.</p>')
        storage_scope = 'browser' if anonymous else 'account'
        storage_contents = ('including originals and prepared frames.' if app.config['RETAIN_ORIGINALS']
                            else 'for prepared frames. Original files are discarded after processing.')
        upload_info = (
            '<div id="uploadInfo"><p class="expiryNotice"><strong>Expires in 7 days — export your CSV.</strong> '
            'Videos and annotations are automatically removed.</p>'
            '<ul class="uploadFacts"><li>MP4 up to 200 MB, 60 seconds, 1920 × 1080, 60 fps. Use 60fps 1080 for best results.</li>'
            f'<li>Storage limit: 1 GB per {storage_scope}, {storage_contents} One upload is prepared at a time.</li>'
        )
        if anonymous:
            upload_info += ('<li>Your uploads are available only in this browser. Clearing cookies or using another '
                            'device starts a new workspace.</li>')
        upload_info += '</ul></div>'
        start = html.index('<div id="uploadInfo">')
        end = html.index('</div>', start) + len('</div>')
        html = html[:start] + upload_info + html[end:]
        return Response(html, mimetype='text/html')

    @app.get('/privacy')
    def privacy():
        return render_template('privacy.html', support_email=app.config['SUPPORT_EMAIL'], anonymous=anonymous,
                               retain_originals=app.config['RETAIN_ORIGINALS'])

    @app.get('/workspace.css')
    @app.get('/labelTool.js')
    def asset():
        name = request.path[1:]
        response = send_file(ROOT / 'web' / name, conditional=True, max_age=31536000)
        response.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
        return response

    def owned_pitch(identifier):
        row = db().execute('SELECT p.*,v.metadata AS video_meta,v.created FROM pitches p JOIN videos v ON v.id=p.video WHERE p.id=? AND v.owner=? AND v.created>=?',
                           (identifier, g.user['id'], time.time() - app.config['RETENTION'])).fetchone()
        if row is None:
            abort(404, description='Pitch not found or expired.')
        pitch = json.loads(row['metadata'])
        if pitch.get('example') and not app.config['EXAMPLES_ENABLED']:
            abort(404, description='This practice clip is unavailable.')
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
        if request.path == '/api/frame':
            dailyBudget('frame', app.config['FRAME_DAILY_LIMIT'], app.config['FRAME_IP_DAILY_LIMIT'])
        directory = request.args.get('dir', '')
        if not directory.startswith('local_'):
            abort(404)
        row, pitch, meta = owned_pitch(directory[6:])
        if request.path == '/api/clip':
            return {key: meta[key] for key in ('nFrames', 'fps', 'frameTimes')}
        index = int(request.args.get('frame', '0'))
        if not 0 <= index < meta['nFrames']:
            abort(404)
        frameSize = request.args.get('size', 'full')
        if frameSize not in ('full', 'scrub'):
            abort(400, description='Frame size must be full or scrub.')
        frameFormat = meta.get('frameFormat', 'jpg')
        suffix = '.scrub.webp' if frameSize == 'scrub' and meta.get('scrubWidth') else '.' + frameFormat
        path = root / 'videos' / row['video'] / f'{index:04d}{suffix}'
        if not path.is_file():
            abort(404)
        response = send_file(path, mimetype='image/webp' if path.suffix == '.webp' else 'image/jpeg',
                             conditional=False, etag=False, max_age=0)
        response.headers['Cache-Control'] = 'no-store, private'
        return response

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
        """Open up to five distinct private practice pitches, preserving prior work."""
        if not app.config['EXAMPLES_ENABLED']:
            abort(404, description='Practice clips are unavailable.')
        if (root / 'uploads.paused').exists():
            abort(503, description='New uploads and practice sessions are temporarily paused. Saved work can still be exported.')
        value = payload()
        if set(value) - {'workspace'}:
            abort(400)
        slugs = example.available(root)
        if not slugs:
            abort(404, description='No practice clip is installed on this server.')
        created = []
        with media_lock(root):
            try:
                with db():
                    db().execute('BEGIN IMMEDIATE')
                    # Recheck inside the lock so retries never duplicate a visitor's clips.
                    mine = {}
                    for pitch in list_pitches():
                        if pitch.get('example') in slugs:
                            mine.setdefault(pitch['example'], pitch)
                    practice = list(mine.values())[:5]
                    for slug in slugs:
                        if len(practice) >= 5:
                            break
                        if slug in mine:
                            continue
                        if shutil.disk_usage(root).free < 1024 ** 3:
                            abort(413, description='Storage is full. Please try again later.')
                        pitch = example.clone(db(), root, g.user['id'], slug, guest=anonymous)
                        created.append(pitch)
                        practice.append(pitch)
            except Exception:
                # The database rolls back the whole set; remove only its new clone folders.
                for pitch in created:
                    shutil.rmtree(root / 'videos' / pitch['sessionId'], ignore_errors=True)
                raise
        return dict(ok=True, pitches=practice, pitch=practice[0], reused=not created)

    @app.get('/api/export')
    def export():
        result = analysisExport.exportCsv(list_labels(), list_pitches(), labelServer.getManualAngleBundle(), request.args.get('session'))
        return Response(result.encode('utf-8-sig'), mimetype='text/csv', headers={'Content-Disposition': 'attachment; filename="arm-angle-session.csv"'})

    @app.post('/api/upload')
    def upload():
        if (root / 'uploads.paused').exists():
            abort(503, description='New uploads are temporarily paused. Saved work can still be exported.')
        multipart = request.mimetype == 'multipart/form-data'
        if not request.content_length or not 0 < request.content_length <= MAX_BYTES + (65536 if multipart else 0):
            abort(413, description='Choose an MP4 up to 200 MB.')
        dailyBudget('upload', app.config['UPLOAD_DAILY_LIMIT'], app.config['UPLOAD_IP_DAILY_LIMIT'])
        if multipart:
            uploaded = request.files.get('video')
            if uploaded is None or len(request.files) != 1 or len(request.files.getlist('video')) != 1:
                abort(400, description='Choose one MP4 video.')
            metadata = request.form
            name = (uploaded.filename or '').replace('\\', '/').split('/')[-1]
            stream = uploaded.stream
        else:
            # Compatibility for existing raw-video API clients. The hosted UI
            # uses multipart fields so personal metadata never enters its URL.
            metadata = request.args
            name = metadata.get('filename', '').replace('\\', '/').split('/')[-1]
            stream = request.stream
        if not name.lower().endswith('.mp4') or len(name) > 240:
            abort(400, description='Choose an MP4 video.')
        throws = metadata.get('pThrows', 'R')
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
            # Reserve this original's declared length plus the bounded frame budget.
            # The media lock prevents two preparations from spending the same space.
            reserve = min(request.content_length, MAX_BYTES) + MAX_FRAME_BYTES
            if used + reserve > app.config['USER_QUOTA']:
                abort(413, description='Your storage is full. Delete an older session from ' + ('My uploads' if anonymous else 'My account') + ' and retry.')
            if total + reserve > app.config['TOTAL_QUOTA'] or shutil.disk_usage(root).free < reserve + 1024**3:
                abort(503, description='Server storage is temporarily full. Please try again later.')
            video = uuid.uuid4().hex
            directory = root / 'videos' / video
            directory.mkdir(mode=0o700)
            registered = False
            try:
                with (directory / 'source.mp4').open('xb') as handle:
                    received = 0
                    while True:
                        chunk = stream.read(min(1024 * 1024, MAX_BYTES + 1 - received))
                        if not chunk:
                            break
                        received += len(chunk)
                        if received > MAX_BYTES:
                            abort(413, description='Choose an MP4 up to 200 MB.')
                        handle.write(chunk)
                    if not received or (not multipart and received != request.content_length):
                        abort(400, description='Upload was interrupted.')
                result = subprocess.run([sys.executable, '-m', 'hosted.video', str(directory)], cwd=ROOT,
                                        capture_output=True, text=True, timeout=100,
                                        env={**os.environ, 'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1'})
                if result.returncode:
                    abort(400, description=result.stderr.strip()[-300:] or 'Video preparation failed.')
                meta = json.loads((directory / 'meta.json').read_text())
                size = sum(p.stat().st_size for p in directory.iterdir())
                details = dict(sourceName=name, sessionName=metadata.get('sessionName', '')[:160] or name,
                               pitcherName=metadata.get('pitcherName', '')[:160], pThrows=throws)
                if not app.config['RETAIN_ORIGINALS']:
                    (directory / 'source.mp4').unlink()
                    meta['original'] = dict(kind='discarded')
                    (directory / 'meta.json').write_text(json.dumps(meta))
                    size = sum(p.stat().st_size for p in directory.iterdir())
                elif app.config['S3_BUCKET']:
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
                if app.config['RETAIN_ORIGINALS'] and app.config['S3_BUCKET']:
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

    def remove_videos(identifiers):
        from hosted import deletions
        try:
            deletions.record(root, identifiers, app.config['DELETION_BUCKET'], app.config['DELETION_REGION'])
        except Exception:
            app.logger.exception('Could not persist deletion records')
            abort(503, description='Deletion could not be confirmed. Please retry; recovery protection is temporarily unavailable.')
        with db():
            for identifier in identifiers:
                db().execute("UPDATE s3_objects SET state='delete' WHERE video=?", (identifier,))
                db().execute('DELETE FROM videos WHERE id=?', (identifier,))
        local_pending = False
        for identifier in identifiers:
            directory = root / 'videos' / identifier
            shutil.rmtree(directory, ignore_errors=True)
            local_pending |= directory.exists()
        remote_pending = bool(storage.drain_deletions(root))
        return local_pending or remote_pending

    @app.post('/api/delete-all')
    def delete_all():
        payload()
        with media_lock(root):
            identifiers = [r['id'] for r in db().execute('SELECT id FROM videos WHERE owner=?', (g.user['id'],))]
            pending = remove_videos(identifiers)
        session.clear()
        response = jsonify(ok=True, storageDeletionPending=pending)
        response.headers['Clear-Site-Data'] = '"cache", "cookies", "storage"'
        return response

    @app.post('/api/delete-session')
    def delete_session():
        value = payload()
        with media_lock(root):
            row = db().execute('SELECT id FROM videos WHERE id=? AND owner=?', (value.get('sessionId'), g.user['id'])).fetchone()
            if not row:
                abort(404)
            pending = remove_videos([row['id']])
        return dict(ok=True, storageDeletionPending=bool(pending))

    @app.get('/account.js')
    def account_js():
        return Response((ROOT / 'hosted/account.js').read_text(), mimetype='text/javascript')

    return app
