"""Separate practice server. Exports are written only into this folder."""
import argparse
import json
import uuid
import re
import hashlib
from urllib.parse import urlsplit, parse_qs
import cv2
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
ROOT = Path(__file__).resolve().parent

class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        url = urlsplit(self.path)
        if url.path != '/api/frame':
            return super().do_GET()
        try:
            query = parse_qs(url.query)
            pitch_id = query['id'][0]
            frame = int(query['frame'][0])
            allowed = json.loads((ROOT / 'assets/session.json').read_text())['items']
            item = next(p for p in allowed if p['id'] == pitch_id)
            if not 0 <= frame < item['nFrames']:
                raise ValueError('Frame outside clip')
            source = ROOT.parent / 'reports/independent_eval'
            pitches = json.loads((source / 'evalSet.json').read_text())['pitches']
            pitch = next(p for p in pitches if p['evalId'] == pitch_id)
            clip = (source / pitch['clipPath']).resolve()
            if not clip.is_relative_to((source / 'clips').resolve()):
                raise ValueError('Invalid clip')
            cap = cv2.VideoCapture(str(clip))
            try:
                cap.set(cv2.CAP_PROP_POS_FRAMES, frame)
                ok, image = cap.read()
            finally:
                cap.release()
            if not ok:
                raise ValueError('Cannot decode frame')
            ok, encoded = cv2.imencode('.png', image)
            if not ok:
                raise ValueError('Cannot encode frame')
            body = encoded.tobytes()
        except (KeyError, ValueError, StopIteration):
            self.send_error(400, 'Frame unavailable')
            return
        self.send_response(200)
        self.send_header('Content-Type', 'image/png')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('X-Frame-Sha256', hashlib.sha256(body).hexdigest())
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != '/api/export':
            self.send_error(404)
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 1_000_000:
                raise ValueError('Invalid export size')
            payload = json.loads(self.rfile.read(length))
            if payload.get('protocol') != 'fixed-frame-human-repeat' or not isinstance(payload.get('session', {}).get('records'), list):
                raise ValueError('Invalid practice export')
        except (ValueError, TypeError, AttributeError):
            self.send_error(400, 'Invalid practice export')
            return
        destination = ROOT / 'exports'
        destination.mkdir(exist_ok=True)
        filename = f'practice-{uuid.uuid4()}.json'
        (destination / filename).write_text(json.dumps(payload, indent=2) + '\n')
        body = json.dumps({'url': '/exports/' + filename, 'file': 'training-lab/exports/' + filename}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self):
        self.send_header('Cache-Control', 'no-store')
        if re.fullmatch(r'/exports/practice-[0-9a-f-]+\.json', self.path):
            self.send_header('Content-Disposition', 'attachment; filename="' + self.path.rsplit('/', 1)[1] + '"')
        super().end_headers()

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8790)
    args = parser.parse_args()
    if not (ROOT / 'assets/session.json').exists():
        parser.error('Prepare images first: python training-lab/prepare.py')
    with ThreadingHTTPServer(('127.0.0.1', args.port), partial(Handler, directory=str(ROOT))) as server:
        print(f'Practice site: http://127.0.0.1:{server.server_port}', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
