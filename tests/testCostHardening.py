"""Budgets, pre-decode rejection, privacy and conditional media regression tests."""
from concurrent.futures import ThreadPoolExecutor
import io
import json
from pathlib import Path
import time
import unittest
from unittest.mock import patch

import test_hosted

class CostHardeningTest(unittest.TestCase):
    setUp = test_hosted.HostedTest.setUp
    tearDown = test_hosted.HostedTest.tearDown
    login = test_hosted.HostedTest.login
    upload = test_hosted.HostedTest.upload

    def testPrivateCacheAndSecurity(self):
        import cv2
        import numpy as np
        pitch = self.upload()
        url = '/api/frame?dir='+pitch['dirName']
        with self.alice.get(url) as full, self.alice.get(url+'&size=scrub') as scrub:
            self.assertEqual(full.status_code,200)
            self.assertEqual(full.mimetype,'image/webp')
            self.assertEqual(full.headers['Cache-Control'],'no-store, private')
            self.assertNotIn('ETag', full.headers)
            decoded = cv2.imdecode(np.frombuffer(full.data,np.uint8),cv2.IMREAD_COLOR)
            preview = cv2.imdecode(np.frombuffer(scrub.data,np.uint8),cv2.IMREAD_COLOR)
            self.assertEqual(decoded.shape,(180,320,3))
            self.assertEqual(preview.shape,(180,320,3))  # Never upscale small videos.
            etag = 'old-cached-frame'
            self.assertEqual(full.headers['X-Frame-Options'],'DENY')
            self.assertEqual(full.headers['X-Content-Type-Options'],'nosniff')
            self.assertEqual(full.headers['Referrer-Policy'],'same-origin')
            self.assertEqual(full.headers['Content-Security-Policy'],
                "default-src 'self'; img-src 'self' blob: data:; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
        with self.alice.get(url,headers={'If-None-Match':etag}) as cached:
            self.assertEqual(cached.status_code,200)
            self.assertTrue(cached.data)
        self.assertEqual(self.app.test_client().get(url,headers={'If-None-Match':etag}).status_code,401)
        self.assertEqual(self.bob.get(url,headers={'If-None-Match':etag}).status_code,404)
        for asset in ('/workspace.css?v=hardening','/labelTool.js?v=hardening'):
            with self.alice.get(asset) as response:
                self.assertEqual(response.headers['Cache-Control'],'public, max-age=31536000, immutable')
                self.assertIn('ETag',response.headers)
                self.assertNotIn('Set-Cookie',response.headers)
            with self.app.test_client().get(asset) as response:
                self.assertEqual(response.status_code,200)
                self.assertNotIn('Set-Cookie',response.headers)
        self.assertIn('no-store',self.alice.get('/api/pitches').headers['Cache-Control'])
        deleted = self.alice.post('/api/delete-session',json={'sessionId':pitch['sessionId']},headers={'X-CSRF-Token':self.a})
        self.assertEqual(deleted.status_code,200)
        self.assertEqual(self.alice.get(url,headers={'If-None-Match':etag}).status_code,404)

    def testFrameBudgetAtomicPersistentAndResets(self):
        from hosted.app import create_app, connect
        pitch = self.upload()
        self.app.config.update(FRAME_DAILY_LIMIT=5,FRAME_IP_DAILY_LIMIT=100)
        url = '/api/frame?dir='+pitch['dirName']
        cookie = self.alice.get_cookie('armangle_session').value
        def call(_):
            client = self.app.test_client()
            client.set_cookie('armangle_session',cookie)
            with client.get(url) as response:
                return response.status_code, response.headers.get('Retry-After'), response.json if response.is_json else None
        with ThreadPoolExecutor(max_workers=20) as pool:
            results = list(pool.map(call,range(20)))
        self.assertEqual(sum(status==200 for status,_,_ in results),5)
        self.assertEqual(sum(status==429 for status,_,_ in results),15)
        for status, retry, body in results:
            if status==429:
                self.assertTrue(0<int(retry)<=86400)
                self.assertIn('Daily frame limit',body['error'])
        restarted = create_app(dict(self.config,FRAME_DAILY_LIMIT=5,FRAME_IP_DAILY_LIMIT=100)).test_client()
        restarted.set_cookie('armangle_session',cookie)
        self.assertEqual(restarted.get(url).status_code,429)
        with connect(self.root) as db:
            db.execute('UPDATE dailyBudgets SET resetAt=?',(int(time.time())-1,))
        with restarted.get(url) as response:
            self.assertEqual(response.status_code,200)

    def testCookieRotationCannotResetAddressBudget(self):
        from hosted.app import create_app
        app = create_app(dict(self.config,ACCESS_MODE='anonymous',FRAME_IP_DAILY_LIMIT=2))
        for expected in (404,404,429):
            client = app.test_client()
            client.get('/analyze.html')
            response = client.get('/api/frame?dir=local_missing')
            self.assertEqual(response.status_code,expected)
        # A genuinely different address has its own budget.
        client = app.test_client();client.get('/analyze.html')
        self.assertEqual(client.get('/api/frame?dir=local_missing',headers={'X-Forwarded-For':'198.51.100.22'}).status_code,404)

    def testDailyUploadBudgetAndNoBodyReadOnCsrfFailure(self):
        self.app.config['UPLOAD_DAILY_LIMIT']=1
        self.upload()
        with patch('hosted.app.subprocess.run') as decoder:
            response = self.alice.post('/api/upload?filename=second.mp4',data=self.video,headers={'X-CSRF-Token':self.a})
            self.assertEqual(response.status_code,429)
            self.assertIn('Retry-After',response.headers)
            decoder.assert_not_called()
        class unreadableBody(io.BytesIO):
            def read(self,*args):
                raise AssertionError('CSRF rejection must precede body reads')
            def readinto(self,*args):
                raise AssertionError('CSRF rejection must precede body reads')
        for path in ('/api/upload?filename=bad.mp4','/api/label','/api/predict-angle','/api/example'):
            result = self.alice.open(path,method='POST',environ_overrides={
                'wsgi.input':unreadableBody(b'no'), 'CONTENT_LENGTH':'2', 'CONTENT_TYPE':'application/json'})
            self.assertEqual(result.status_code,403)

    def testOversizeRejectedBeforeDecoder(self):
        from hosted.video import MAX_BYTES
        with patch('hosted.app.subprocess.run') as decoder:
            result = self.alice.open('/api/upload?filename=large.mp4',method='POST',headers={'X-CSRF-Token':self.a},
                environ_overrides={'CONTENT_LENGTH':str(MAX_BYTES+1),'wsgi.input':io.BytesIO(b'')})
            self.assertEqual(result.status_code,413)
            self.assertIn('200 MB',result.json['error'])
            decoder.assert_not_called()

    def testDurationRejectedByFfprobeBeforeAnyDecode(self):
        import cv2
        import numpy as np
        from hosted.video import prepare, probeDuration
        folder = Path(self.tmp.name)/'longClip';folder.mkdir()
        source = folder/'source.mp4'
        writer = cv2.VideoWriter(str(source),cv2.VideoWriter_fourcc(*'mp4v'),1,(32,32))
        for _ in range(61):
            writer.write(np.zeros((32,32,3),dtype=np.uint8))
        writer.release()
        with patch('cv2.VideoCapture') as decoder:
            with self.assertRaisesRegex(ValueError,'60 seconds'):
                prepare(folder)
            decoder.assert_not_called()
        result = self.alice.post('/api/upload?filename=long.mp4',data=source.read_bytes(),headers={'X-CSRF-Token':self.a})
        self.assertEqual(result.status_code,400)
        self.assertIn('60 seconds',result.json['error'])
        self.assertEqual(list((self.root/'videos').iterdir()),[])
        for duration in ('NaN','inf','N/A','-1'):
            with patch('hosted.video.subprocess.run') as probe:
                probe.return_value.stdout=json.dumps({'format':{'duration':duration},'streams':[{}]})
                with self.assertRaises(ValueError):
                    probeDuration(source)

    def testLegacyTemplateUpgradePreservesExistingPrivateFrames(self):
        import cv2
        from hosted import example
        from hosted.app import connect
        bundle = test_hosted.HostedTest.example_bundle(self)
        example.install_bundle(self.root,bundle)
        folder = self.root/'examples'/'mlb-test'
        meta = json.loads((folder/'meta.json').read_text())
        # Emulate a pre-upgrade shared JPEG template and an existing visitor.
        for index in range(meta['nFrames']):
            pixels = cv2.imread(str(folder/f'{index:04d}.webp'))
            cv2.imwrite(str(folder/f'{index:04d}.jpg'),pixels)
            (folder/f'{index:04d}.webp').unlink()
            (folder/f'{index:04d}.scrub.webp').unlink()
        del meta['frameFormat'];del meta['scrubWidth']
        (folder/'meta.json').write_text(json.dumps(meta))
        with connect(self.root) as db:
            legacy = example.clone(db,self.root,'alice','mlb-test')
        oldFrame = self.root/'videos'/legacy['sessionId']/'0000.jpg'
        oldBytes = oldFrame.read_bytes()
        self.assertEqual(example.install_bundle(self.root,bundle),['mlb-test'])
        self.assertTrue((folder/'0000.webp').is_file())
        self.assertTrue((folder/'0000.scrub.webp').is_file())
        self.assertEqual(oldFrame.read_bytes(),oldBytes)
        with self.alice.get('/api/frame?dir='+legacy['dirName']) as response:
            self.assertEqual(response.mimetype,'image/jpeg')
            self.assertEqual(response.data,oldBytes)
        self.assertEqual(example.install_bundle(self.root,bundle),[])
