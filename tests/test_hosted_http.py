"""Run the packaged hosted app through real Gunicorn using disposable data.

Emulates Caddy's trusted loopback headers; does not test TLS or the Linux units.
"""
import csv
import http.cookies
import io
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, build_opener, HTTPRedirectHandler
import zipfile

try:
    import flask
    import gunicorn
except ImportError:
    flask = gunicorn = None


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Client:
    def __init__(self, base, cloudfront=False):
        self.base = base
        self.cloudfront = cloudfront
        self.cookie = ''
        self.csrf = ''
        self.opener = build_opener(NoRedirect())

    def request(self, path, data=None, headers=None):
        supplied = {'Host':'beta.example', 'X-Forwarded-Proto':'https', 'X-Forwarded-For':'127.0.0.1'}
        if self.cloudfront:
            supplied.update({'X-Forwarded-Proto':'http', 'CloudFront-Forwarded-Proto':'https',
                             'CloudFront-Viewer-Address':'198.51.100.20:54321'})
        if self.cookie:
            supplied['Cookie'] = self.cookie
        if isinstance(data, dict):
            data = json.dumps(data).encode()
            supplied['Content-Type'] = 'application/json'
        if data is not None:
            supplied.update({'Origin':'https://beta.example', 'X-CSRF-Token':self.csrf})
        supplied.update(headers or {})
        req = Request(self.base + path, data=data, headers=supplied)
        try:
            result = self.opener.open(req, timeout=30)
        except HTTPError as error:
            result = error
        with result:
            body = result.read()
            if result.headers.get('Set-Cookie'):
                cookie = http.cookies.SimpleCookie(result.headers['Set-Cookie'])
                self.cookie = '; '.join(key+'='+morsel.value for key,morsel in cookie.items())
            return result.status, result.headers, body

    def login(self, username):
        from urllib.parse import urlencode
        status, headers, body = self.request('/login')
        assert status == 200
        assert 'Secure' in headers['Set-Cookie'] and 'HttpOnly' in headers['Set-Cookie']
        self.csrf = re.search(rb'name="csrf" value="([^"]+)"',body)[1].decode()
        data = urlencode(dict(username=username,password='temporary-http-test-password',csrf=self.csrf)).encode()
        status, _, _ = self.request('/login',data,{'Content-Type':'application/x-www-form-urlencoded'})
        assert status == 302
        status, _, body = self.request('/analyze.html')
        assert status == 200
        self.csrf = re.search(rb'name="csrf-token" content="([^"]+)"',body)[1].decode()

    def open_workspace(self):
        status, headers, body = self.request('/analyze.html')
        assert status == 200
        assert 'Secure' in headers['Set-Cookie'] and 'HttpOnly' in headers['Set-Cookie']
        assert b'My uploads' in body and b'My account' not in body
        self.csrf = re.search(rb'name="csrf-token" content="([^"]+)"',body)[1].decode()


@unittest.skipIf(flask is None or gunicorn is None, 'Install requirements-hosted.txt')
class HostedHTTPTest(unittest.TestCase):
    platform = "lightsail"
    access = 'accounts'
    proxy = 'single'
    def test_packaged_production_server(self):
        import cv2
        import numpy as np
        from werkzeug.security import generate_password_hash
        from hosted.app import initialize, connect
        if self.platform == "beanstalk":
            from tools.prepareBeanstalk import build
        else:
            from tools.prepareHosted import build
        with tempfile.TemporaryDirectory(prefix='armangle-gunicorn-') as tmp:
            root = Path(tmp)
            build(root/'runtime.zip')
            with zipfile.ZipFile(root/'runtime.zip') as archive:
                archive.extractall(root/'runtime')
            initialize(root/'data')
            with connect(root/'data') as db:
                for name in ('alice','bob'):
                    db.execute('INSERT INTO users(id,username,password) VALUES (?,?,?)',
                               (name,name,generate_password_hash('temporary-http-test-password',method='pbkdf2:sha256:1000')))
            with socket.socket() as sock:
                sock.bind(('127.0.0.1',0))
                port = sock.getsockname()[1]
            base = f'http://127.0.0.1:{port}'
            env = {**os.environ,'ARMANGLE_DATA':str(root/'data'),'ARMANGLE_SECRET':secrets.token_hex(32),
                   'ARMANGLE_ACCESS':self.access,
                   'ARMANGLE_PROXY':self.proxy,
                   'ARMANGLE_ORIGIN':'https://beta.example','OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1',
                   'PYTHONPATH':str(root/'runtime')}
            process = None
            log = (root/'gunicorn.log').open('w+')

            def start():
                nonlocal process
                process = subprocess.Popen([sys.executable,'-m','gunicorn','--config',('gunicorn.conf.py' if self.platform == 'beanstalk' else 'deploy/gunicorn.conf.py'),
                                            '--bind',f'127.0.0.1:{port}',('application:application' if self.platform == 'beanstalk' else 'hosted.app:create_app()')],
                                           cwd=root/'runtime',env=env,stdout=log,stderr=log)
                for _ in range(150):
                    if process.poll() is not None:
                        break
                    try:
                        if Client(base).request('/healthz')[0] == 200:
                            return
                    except OSError:
                        pass
                    time.sleep(.1)
                log.flush()
                self.fail('Gunicorn did not become ready: '+(root/'gunicorn.log').read_text())

            def stop():
                if process and process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)

            try:
                start()
                alice, bob, guest = [Client(base, cloudfront=self.proxy == 'cloudfront-vpc') for _ in range(3)]
                self.assertEqual(guest.request('/api/pitches')[0],401)
                self.assertEqual(guest.request('/login',headers={'Host':'evil.example'})[0],400)
                if self.access == 'anonymous':
                    alice.open_workspace()
                    bob.open_workspace()
                    self.assertEqual(alice.request('/login')[0],302)
                    self.assertEqual(alice.request('/account')[0],200)
                    self.assertEqual(alice.request('/api/upload?filename=test.mp4', b'bad', {'X-CSRF-Token':'wrong'})[0],403)
                else:
                    alice.login('alice')
                    bob.login('bob')
                fixture = root/'fixture.mp4'
                writer = cv2.VideoWriter(str(fixture),cv2.VideoWriter_fourcc(*'mp4v'),30,(320,180))
                self.assertTrue(writer.isOpened())
                for i in range(12):
                    writer.write(np.full((180,320,3),i*10,dtype=np.uint8))
                writer.release()
                boundary = 'armangle-http-test-boundary'
                multipart = (
                    f'--{boundary}\r\nContent-Disposition: form-data; name="sessionName"\r\n\r\nHTTP-test\r\n'
                    f'--{boundary}\r\nContent-Disposition: form-data; name="video"; filename="test.mp4"\r\n'
                    'Content-Type: video/mp4\r\n\r\n'
                ).encode() + fixture.read_bytes() + f'\r\n--{boundary}--\r\n'.encode()
                status, _, body = alice.request('/api/upload', multipart,
                                               {'Content-Type': 'multipart/form-data; boundary=' + boundary})
                self.assertEqual(status,200,body)
                pitch = json.loads(body)['pitch']
                self.assertEqual(bob.request('/api/frame?dir='+pitch['dirName'])[0],404)
                status, headers, body = alice.request('/api/frame?dir='+pitch['dirName']+'&frame=2')
                self.assertEqual(status,200)
                self.assertEqual(headers['Content-Type'],'image/webp')
                self.assertIn('no-store', headers['Cache-Control'])
                self.assertTrue(body.startswith(b'RIFF') and body[8:12] == b'WEBP')
                row = dict(game_pk='local',play_id=pitch['playId'],frame_index=2,status='labeled',workspace='analysis',
                           shoulder_x=120,shoulder_y=100,off_shoulder_x=150,off_shoulder_y=100,
                           elbow_x=100,elbow_y=70,wrist_x=80,wrist_y=40,ball_x=60,ball_y=20)
                status, _, body = alice.request('/api/predict-angle',row)
                self.assertEqual(status,200,body)
                prediction = json.loads(body)['predicted_arm_angle']
                self.assertEqual(alice.request('/api/label',row)[0],200)
                self.assertEqual(bob.request('/api/label',row)[0],404)
                status, _, exported = alice.request('/api/export?session='+pitch['sessionId'])
                self.assertEqual(status,200)
                result = list(csv.DictReader(io.StringIO(exported.decode('utf-8-sig'))))
                self.assertAlmostEqual(float(result[0]['predicted_arm_angle_deg']),prediction)
                stop()
                start()
                self.assertEqual(alice.request('/api/export?session='+pitch['sessionId'])[2],exported)
                self.assertEqual(alice.request('/api/delete-session',dict(sessionId=pitch['sessionId']))[0],200)
                self.assertEqual(alice.request('/api/frame?dir='+pitch['dirName'])[0],404)
                self.assertEqual(alice.request('/logout',b'csrf='+alice.csrf.encode(),{'Content-Type':'application/x-www-form-urlencoded'})[0],302)
                self.assertEqual(alice.request('/api/labels')[0],401)
                if self.access == 'anonymous':
                    alice.open_workspace()
                    self.assertEqual(json.loads(alice.request('/api/pitches')[2]), {'pitches':[]})
            finally:
                stop()
                log.close()


class BeanstalkHTTPTest(HostedHTTPTest):
    platform = 'beanstalk'


class AnonymousBeanstalkHTTPTest(BeanstalkHTTPTest):
    access = 'anonymous'


class CloudFrontBeanstalkHTTPTest(AnonymousBeanstalkHTTPTest):
    proxy = 'cloudfront-vpc'


if __name__ == '__main__':
    unittest.main()
