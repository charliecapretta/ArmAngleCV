"""Repeatable local five-demo HTTP workload; no live deployment or AWS access.

Runs the current Flask app on loopback with disposable data. Walks every frame,
locks one release per pitch, saves five points, exports CSV, then conditionally
reloads every frame (simulating a browser cache). --output saves JSON evidence.
"""
import argparse
import http.cookiejar
import json
from pathlib import Path
import re
import sys
import tempfile
import threading
from urllib.error import HTTPError
from urllib.request import build_opener, HTTPCookieProcessor, Request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hosted.app import ROOT, create_app
from hosted.example import install
from werkzeug.serving import make_server, WSGIRequestHandler

class quietHandler(WSGIRequestHandler):
    def log(self, *args, **kwargs):
        pass

def prepareBaseline(directory):
    """Original hosted JPEG q85 encoding, retained solely for reproducible A/B runs."""
    import cv2
    root = Path(directory)
    capture = cv2.VideoCapture(str(root/'source.mp4'))
    fps = capture.get(cv2.CAP_PROP_FPS)
    times = []
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            frameHeight, frameWidth = frame.shape[:2]
            ok, encoded = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY,85])
            if not ok:
                raise RuntimeError('Baseline encoding failed')
            (root/f'{len(times):04d}.jpg').write_bytes(encoded.tobytes())
            times.append(capture.get(cv2.CAP_PROP_POS_MSEC)/1000)
        if not times:
            raise RuntimeError('No baseline frames')
        if not all(b>a for a,b in zip(times,times[1:])):
            times = [i/fps for i in range(len(times))]
        meta = dict(nFrames=len(times),fps=fps,imgW=frameWidth,imgH=frameHeight,frameTimes=times)
    finally:
        capture.release()
    (root/'meta.json').write_text(json.dumps(meta))
    return meta


def measureSession(output=None, baseline=False):
    with tempfile.TemporaryDirectory(prefix='armangle-measure-') as temporary:
        app = create_app(dict(TESTING=True, SECRET_KEY='measurement-only-' * 4,
                             DATA_DIR=temporary, ACCESS_MODE='anonymous',
                             PUBLIC_ORIGIN='http://localhost', SESSION_COOKIE_SECURE=False))
        if baseline:
            # Reproduce the pre-hardening no-store response path for A/B reruns.
            @app.after_request
            def baselineHeaders(response):
                response.headers['Cache-Control'] = 'no-store, private'
                response.headers.pop('ETag', None)
                response.headers.pop('Last-Modified', None)
                return response
        if baseline:
            import hosted.video
            hosted.video.prepare = prepareBaseline
        pitches = sorted(json.loads((ROOT/'uiTestBatch.json').read_text())['pitches'], key=lambda p:p['armAngle'])
        selected = [pitches[round(i*(len(pitches)-1)/4)] for i in range(5)]
        for i, pitch in enumerate(selected):
            source = ROOT/'pitches'/f"{pitch['gamePk']}_{pitch['playId']}"/'clip.mp4'
            install(temporary, source, dict(sessionName=f'Demo {i}', pitcherName=pitch['pitcherName'],
                    pThrows=pitch['pThrows'], armAngle=pitch['armAngle']), slug=f'demo-{i}')
        server = make_server('127.0.0.1', 0, app, request_handler=quietHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        client = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
        stats = dict(totalBytes=0, requestCount=0, frameBytes=0, frameRequests=0, reloadBodyDownloads=0, reload304s=0)
        cache = {}
        csrf = ''
        def request(path, body=None, reload=False):
            headers = {'Host':'localhost'}
            if body is not None:
                headers.update({'X-CSRF-Token':csrf, 'Content-Type':'application/json'})
                body = json.dumps(body).encode()
            if reload and cache.get(path):
                headers['If-None-Match'] = cache[path]
            try:
                response = client.open(Request(f'http://127.0.0.1:{server.server_port}'+path, body, headers))
            except HTTPError as error:
                response = error
            with response:
                data = response.read()
                if response.status not in (200, 304):
                    raise RuntimeError(f'{path}: {response.status} {data[:200]}')
                stats['requestCount'] += 1
                stats['totalBytes'] += len(data)
                if path.startswith('/api/frame'):
                    if reload:
                        stats['reloadBodyDownloads'] += bool(data)
                        stats['reload304s'] += response.status == 304
                    else:
                        stats['frameBytes'] += len(data)
                        stats['frameRequests'] += 1
                        cache[path] = response.headers.get('ETag')
                return data
        try:
            html = request('/analyze.html')
            csrf = re.search(rb'name="csrf-token" content="([^"]+)"',html)[1].decode()
            request('/workspace.css?v=measurement'); request('/labelTool.js?v=measurement')
            pitches = json.loads(request('/api/example', {}))['pitches']
            request('/api/pitches'); request('/api/labels')
            paths = []
            for pitch in pitches:
                request('/api/clip?dir='+pitch['dirName'])
                for frame in range(pitch['nFrames']):
                    path = f"/api/frame?dir={pitch['dirName']}&frame={frame}&size=scrub"
                    request(path); paths.append(path)
                frame = pitch['nFrames']//2
                path = f"/api/frame?dir={pitch['dirName']}&frame={frame}"
                request(path); paths.append(path)
                row = dict(game_pk='local', play_id=pitch['playId'], frame_index=frame, workspace='analysis', status='labeled',
                           shoulder_x=600,shoulder_y=400,off_shoulder_x=650,off_shoulder_y=400,
                           elbow_x=550,elbow_y=350,wrist_x=500,wrist_y=300,ball_x=480,ball_y=280)
                request('/api/predict-angle',row); request('/api/label',row)
            for pitch in pitches:
                request('/api/export?session='+pitch['sessionId'])
            stats['firstPassBytes'] = stats['totalBytes']
            for path in paths:
                request(path, reload=True)
            stats['averageFrameBytes'] = round(stats['frameBytes']/stats['frameRequests'],2)
            stats['pitchCount'] = len(pitches)
            rendered = json.dumps(stats, indent=2)
            print(rendered)
            if output:
                Path(output).write_text(rendered+'\n')
        finally:
            server.shutdown(); thread.join()

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output')
    parser.add_argument('--baseline', action='store_true', help='Disable caching for the original JPEG baseline')
    args = parser.parse_args()
    measureSession(args.output, args.baseline)
