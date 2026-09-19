"""Hosted isolation, real MP4 workflow, retention, restart and recovery checks."""
import csv
import io
import json
from pathlib import Path
import re
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

try:
    import flask
except ImportError:
    flask = None


@unittest.skipIf(flask is None, 'Install requirements-hosted.txt for hosted checks')
class HostedTest(unittest.TestCase):
    def use_s3(self):
        class FakeS3:
            def __init__(self):
                self.objects = {}
                self.fail_put = False
                self.fail_delete = False
                self.versioning = {}
            def get_bucket_versioning(self, **args):
                return self.versioning
            def put_object(self, **args):
                assert args['ServerSideEncryption'] == 'AES256'
                assert 'ACL' not in args
                data = args['Body'].read()
                assert len(data) == args['ContentLength']
                self.objects[(args['Bucket'],args['Key'])] = data
                if self.fail_put:
                    raise OSError('Simulated lost response after S3 stored the object')
                return {}
            def delete_object(self, **args):
                if self.fail_delete:
                    raise OSError('Simulated temporary S3 outage')
                self.objects.pop((args['Bucket'],args['Key']),None)
                return {}
        fake = FakeS3()
        patched = patch('hosted.storage.client',return_value=fake)
        patched.start()
        self.addCleanup(patched.stop)
        self.app.config.update(S3_BUCKET='armangle-test-only', S3_REGION='us-east-2')
        return fake

    def setUp(self):
        import cv2
        import numpy as np
        from hosted.app import create_app, connect
        from werkzeug.security import generate_password_hash
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / 'data'
        self.config = dict(TESTING=True, SECRET_KEY='a' * 64, DATA_DIR=str(self.root), PUBLIC_ORIGIN='http://localhost', SESSION_COOKIE_SECURE=False)
        self.app = create_app(self.config)
        with connect(self.root) as db:
            for user in ('alice', 'bob'):
                db.execute('INSERT INTO users(id,username,password) VALUES (?,?,?)', (user, user, generate_password_hash('a-long-test-password', method='pbkdf2:sha256:1000')))
        self.alice = self.app.test_client()
        self.bob = self.app.test_client()
        self.a = self.login(self.alice, 'alice')
        self.b = self.login(self.bob, 'bob')
        video = Path(self.tmp.name) / 'fixture.mp4'
        writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*'mp4v'), 30, (320, 180))
        self.assertTrue(writer.isOpened())
        for i in range(12):
            frame = np.zeros((180, 320, 3), dtype=np.uint8)
            cv2.circle(frame, (100+i, 80), 5, (255,255,255), -1)
            writer.write(frame)
        writer.release()
        self.video = video.read_bytes()

    def tearDown(self):
        self.tmp.cleanup()

    def login(self, client, username):
        page = client.get('/login').text
        token = re.search(r'name="csrf" value="([^"]+)"', page)[1]
        result = client.post('/login', data=dict(username=username,password='a-long-test-password',csrf=token))
        self.assertEqual(result.status_code, 302)
        with client.session_transaction() as s:
            return s['csrf']

    def post(self, client, token, path, body):
        return client.post(path, json=body, headers={'X-CSRF-Token':token})

    def upload(self):
        result = self.alice.post('/api/upload?filename=test.mp4&sessionName=Test',data=self.video,content_type='video/mp4',headers={'X-CSRF-Token':self.a})
        self.assertEqual(result.status_code,200,result.text)
        return result.json['pitch']

    def row(self,pitch,status='labeled'):
        return dict(game_pk='local',play_id=pitch['playId'],workspace='analysis',frame_index=2,status=status,
                    shoulder_x=120,shoulder_y=100,off_shoulder_x=150,off_shoulder_y=100,
                    elbow_x=100,elbow_y=70,wrist_x=80,wrist_y=40,ball_x=60,ball_y=20,
                    release_quality='closest_available',point_quality='reviewed')

    def test_workflow_isolation_restart_and_backup(self):
        from hosted.app import create_app, connect
        from hosted.manage import backup, restore
        import angleInputs
        import labelServer
        p = self.upload()
        self.assertNotIn('sourcePath',p)
        directory=p['dirName']
        self.assertEqual(self.bob.get('/api/pitches').json,{'pitches':[]})
        for endpoint in ('/api/clip','/api/frame'):
            self.assertEqual(self.bob.get(endpoint+'?dir='+directory).status_code,404)
        self.assertEqual(self.alice.get('/api/frame?dir='+directory+'&frame=2').status_code,200)
        self.assertEqual(self.alice.get('/api/clip?dir='+directory).json['nFrames'],12)
        for endpoint in ('/api/label','/api/predict-angle','/api/delete-label'):
            self.assertEqual(self.post(self.bob,self.b,endpoint,self.row(p)).status_code,404)
        self.assertEqual(self.post(self.bob,self.b,'/api/import',dict(parentId=p['playId'])).status_code,404)
        saved=self.post(self.alice,self.a,'/api/label',self.row(p))
        self.assertEqual(saved.status_code,200,saved.text)
        token=saved.json['undoToken']
        self.assertEqual(self.post(self.bob,self.b,'/api/undo',dict(undoToken=token)).status_code,404)
        predict=self.post(self.alice,self.a,'/api/predict-angle',self.row(p))
        self.assertEqual(predict.status_code,200,predict.text)
        persisted=self.alice.get('/api/labels').json['labels'][0]
        expected=float(angleInputs.predict(labelServer.getManualAngleBundle(),[persisted])[0])
        self.assertAlmostEqual(predict.json['predicted_arm_angle'],expected)
        for start in (4,7):
            added=self.post(self.alice,self.a,'/api/import',dict(parentId=p['playId'],startFrame=start))
            self.assertEqual(added.status_code,200,added.text)
            self.assertEqual(added.json['pitch']['startFrame'],start)
            self.assertEqual(self.post(self.alice,self.a,'/api/label',self.row(added.json['pitch'],'release_selected')).status_code,200)
        export=self.alice.get('/api/export?session='+p['sessionId'])
        rows=list(csv.DictReader(io.StringIO(export.data.decode('utf-8-sig'))))
        self.assertEqual(len(rows),3)
        self.assertAlmostEqual(float(next(r for r in rows if r['status']=='labeled')['predicted_arm_angle_deg']),expected)
        self.assertNotEqual(self.bob.get('/api/export?session='+p['sessionId']).status_code,200)
        restarted=create_app(self.config).test_client()
        token2=self.login(restarted,'alice')
        self.assertEqual(len(restarted.get('/api/labels').json['labels']),3)
        archive=Path(self.tmp.name)/'backup.tar.gz'
        backup(self.root,archive)
        restored=Path(self.tmp.name)/'restored'
        restore(archive,restored)
        recovered=create_app(dict(self.config,DATA_DIR=str(restored))).test_client()
        self.login(recovered,'alice')
        self.assertEqual(recovered.get('/api/export?session='+p['sessionId']).data,export.data)
        self.assertEqual(recovered.get('/api/frame?dir='+directory+'&frame=2').status_code,200)
        self.assertEqual(self.post(restarted,token2,'/api/undo',dict(undoToken=token)).status_code,200)
        self.assertEqual(len(restarted.get('/api/labels').json['labels']),2)
        self.assertEqual(self.post(self.bob,self.b,'/api/delete-session',dict(sessionId=p['sessionId'])).status_code,404)
        self.assertEqual(self.post(self.alice,self.a,'/api/delete-session',dict(sessionId=p['sessionId'])).status_code,200)
        self.assertFalse((self.root/'videos'/p['sessionId']).exists())
        self.assertEqual(self.alice.get('/api/labels').json['labels'],[])

    def test_auth_csrf_allowlist_and_login_limit(self):
        guest=self.app.test_client()
        self.assertEqual(guest.get('/api/pitches').status_code,401)
        for path in ('/labelServer.py','/labels.csv','/.git/config','/uploads/test.mp4','/labelTool.html','/api/pose','/api/ball','/api/prefetch'):
            self.assertEqual(self.alice.get(path).status_code,404,path)
        self.assertEqual(self.alice.post('/api/import',json={}).status_code,403)
        self.assertEqual(self.post(self.alice,self.a,'/api/import',dict(path='/etc/passwd')).status_code,400)
        self.assertEqual(self.alice.post('/api/import',json={},headers={'X-CSRF-Token':self.a,'Origin':'https://evil.example'}).status_code,403)
        login=guest.get('/login').text
        token=re.search(r'name="csrf" value="([^"]+)"',login)[1]
        for _ in range(11):
            result=guest.post('/login',data=dict(csrf=token,username='unknown',password='bad'))
        self.assertEqual(result.status_code,429)
        from hosted.app import connect
        with connect(self.root) as db:
            db.execute("UPDATE users SET version=version+1 WHERE id='alice'")
        self.assertEqual(self.alice.get('/api/pitches').status_code,401)

    def test_upload_limits_busy_and_expiry(self):
        from hosted.app import connect,cleanup,media_lock
        with media_lock(self.root):
            result=self.alice.post('/api/upload?filename=test.mp4',data=self.video,headers={'X-CSRF-Token':self.a})
            self.assertEqual(result.status_code,429)
        bad=self.alice.post('/api/upload?filename=test.mp4',data=b'not video',headers={'X-CSRF-Token':self.a})
        self.assertEqual(bad.status_code,400)
        self.assertEqual(list((self.root/'videos').iterdir()),[])
        p=self.upload()
        self.app.config['USER_QUOTA']=1
        blocked=self.alice.post('/api/upload?filename=test.mp4',data=self.video,headers={'X-CSRF-Token':self.a})
        self.assertEqual(blocked.status_code,413)
        with connect(self.root) as db:
            db.execute('UPDATE videos SET created=?',(time.time()-8*86400,))
        self.assertEqual(self.alice.get('/api/pitches').json['pitches'],[])
        self.assertEqual(self.alice.get('/api/frame?dir='+p['dirName']).status_code,404)
        with media_lock(self.root):
            cleanup(self.root)
        self.assertFalse((self.root/'videos'/p['sessionId']).exists())

    def test_successful_logins_do_not_lock_out_shared_ip(self):
        for _ in range(12):
            self.a = self.login(self.alice, 'alice')
        self.b = self.login(self.bob, 'bob')
        self.assertEqual(self.bob.get('/api/pitches').status_code, 200)

    def test_restore_rejects_empty_or_missing_video_backup(self):
        import tarfile
        from hosted.manage import restore, backup
        empty = Path(self.tmp.name) / 'empty.tar.gz'
        with tarfile.open(empty, 'w:gz'):
            pass
        destination = Path(self.tmp.name) / 'invalid-restore'
        with self.assertRaises(ValueError):
            restore(empty, destination)
        self.assertFalse(destination.exists())
        pitch = self.upload()
        complete = Path(self.tmp.name) / 'complete.tar.gz'
        broken = Path(self.tmp.name) / 'broken.tar.gz'
        backup(self.root, complete)
        with tarfile.open(complete) as source, tarfile.open(broken, 'w:gz') as target:
            for item in source.getmembers():
                if item.name.endswith('/0000.jpg'):
                    continue
                target.addfile(item, source.extractfile(item) if item.isfile() else None)
        with self.assertRaises(ValueError):
            restore(broken, destination)
        self.assertFalse(destination.exists())

    def test_configuration_and_model_fail_closed(self):
        from hosted.app import create_app
        with self.assertRaises(RuntimeError):
            create_app(dict(self.config, SECRET_KEY='REPLACE_WITH_RANDOM_SECRET_AT_LEAST_32_CHARACTERS'))
        for origin in ('https://example.com/path', 'http://example.com', 'https://user:pass@example.com'):
            with self.assertRaises(RuntimeError):
                create_app(dict(self.config, TESTING=False, PUBLIC_ORIGIN=origin))
        from unittest.mock import patch
        class WrongHash:
            def hexdigest(self):
                return 'wrong'
        with patch('hosted.app.hashlib.sha256', return_value=WrongHash()):
            with self.assertRaises(RuntimeError):
                create_app(self.config)

    def test_stale_undo_cannot_overwrite_newer_save(self):
        p=self.upload()
        first=self.post(self.alice,self.a,'/api/label',self.row(p)).json['undoToken']
        changed=dict(self.row(p),ball_x=65)
        self.assertEqual(self.post(self.alice,self.a,'/api/label',changed).status_code,200)
        self.assertEqual(self.post(self.alice,self.a,'/api/undo',dict(undoToken=first)).status_code,409)
        invalid=dict(self.row(p),frame_index=999)
        self.assertEqual(self.post(self.alice,self.a,'/api/label',invalid).status_code,400)
        self.assertEqual(self.alice.get('/api/labels').json['labels'][0]['ball_x'],65)

    def test_anonymous_ownership_cookie_loss_and_expiry(self):
        from hosted.app import create_app, connect, cleanup, media_lock
        app = create_app(dict(self.config, ACCESS_MODE='anonymous'))
        alice, bob = app.test_client(), app.test_client()
        tokens = []
        for client in (alice, bob):
            page = client.get('/analyze.html')
            self.assertEqual(page.status_code, 200)
            self.assertIn('My uploads', page.text)
            self.assertNotIn('My account', page.text)
            with client.session_transaction() as state:
                tokens.append(state['csrf'])
        with connect(self.root) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM users WHERE password='!'").fetchone()[0], 0)
        response = alice.post('/api/upload?filename=guest.mp4', data=self.video, headers={'X-CSRF-Token':tokens[0]})
        self.assertEqual(response.status_code, 200, response.text)
        p = response.json['pitch']
        self.assertEqual(self.post(alice,tokens[0],'/api/label',self.row(p)).status_code,200)
        for endpoint in ('/api/clip', '/api/frame'):
            self.assertEqual(bob.get(endpoint+'?dir='+p['dirName']).status_code,404)
        for endpoint in ('/api/label', '/api/predict-angle', '/api/delete-label'):
            self.assertEqual(self.post(bob,tokens[1],endpoint,self.row(p)).status_code,404)
        self.assertEqual(self.post(bob,tokens[1],'/api/delete-session',dict(sessionId=p['sessionId'])).status_code,404)
        self.assertNotEqual(bob.get('/api/export?session='+p['sessionId']).status_code,200)
        self.assertEqual(alice.get('/api/export?session='+p['sessionId']).status_code,200)
        self.assertNotIn('Sign out', alice.get('/account').text)
        cookie = alice.get_cookie('armangle_session').value
        forged = app.test_client()
        forged.set_cookie('armangle_session', cookie[:-1] + ('A' if cookie[-1] != 'A' else 'B'))
        self.assertEqual(forged.get('/api/pitches').status_code,401)
        alice.delete_cookie('armangle_session')
        alice.get('/analyze.html')
        self.assertEqual(alice.get('/api/pitches').json, {'pitches':[]})
        self.assertEqual(alice.get('/api/frame?dir='+p['dirName']).status_code,404)
        with connect(self.root) as db:
            db.execute('UPDATE videos SET created=?', (time.time()-8*86400,))
        with media_lock(self.root):
            cleanup(self.root)
        self.assertFalse((self.root/'videos'/p['sessionId']).exists())
        with connect(self.root) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM users WHERE password='!'").fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM users').fetchone()[0], 2)

    def test_anonymous_upload_budget_survives_new_cookie(self):
        from hosted.app import create_app, connect
        app = create_app(dict(self.config, ACCESS_MODE='anonymous'))
        client = app.test_client()
        client.get('/analyze.html')
        with client.session_transaction() as state:
            token = state['csrf']
        self.assertEqual(client.post('/api/upload?filename=test.mp4',data=b'bad').status_code,403)
        result = client.post('/api/upload?filename=test.mp4',data=b'bad',headers={'X-CSRF-Token':token})
        self.assertEqual(result.status_code,400)
        with connect(self.root) as db:
            db.execute("UPDATE attempts SET count=30 WHERE key LIKE 'upload-ip:%'")
        client.delete_cookie('armangle_session')
        client.get('/analyze.html')
        with client.session_transaction() as state:
            token = state['csrf']
        result = client.post('/api/upload?filename=test.mp4',data=self.video,headers={'X-CSRF-Token':token})
        self.assertEqual(result.status_code,429)
        with connect(self.root) as db:
            db.execute("UPDATE attempts SET since=? WHERE key LIKE 'upload-%'", (time.time()-3601,))
        result = client.post('/api/upload?filename=test.mp4',data=self.video,headers={'X-CSRF-Token':token})
        self.assertEqual(result.status_code,200,result.text)

    def test_cloudfront_proxy_requires_generated_headers_and_preserves_viewer(self):
        from hosted.app import create_app
        app = create_app(dict(self.config, TESTING=False, ACCESS_MODE='anonymous',
                              PROXY_MODE='cloudfront-vpc', PUBLIC_ORIGIN='https://beta.example',
                              SESSION_COOKIE_SECURE=True))
        client = app.test_client()
        headers = {'Host':'beta.example', 'CloudFront-Forwarded-Proto':'https',
                   'CloudFront-Viewer-Address':'198.51.100.20:443',
                   'X-Forwarded-Proto':'http', 'X-Forwarded-For':'1.2.3.4',
                   'X-Forwarded-Host':'evil.example'}
        @app.get('/test-viewer')
        def viewer():
            from flask import request
            return dict(address=request.remote_addr, scheme=request.scheme, host=request.host)
        self.assertEqual(client.get('/healthz').status_code,200)
        self.assertEqual(client.get('/analyze.html',headers={'Host':'beta.example','X-Forwarded-Proto':'https'}).status_code,403)
        page = client.get('/analyze.html',headers=headers)
        self.assertEqual(page.status_code,200,page.text)
        self.assertIn('Secure',page.headers['Set-Cookie'])
        result = client.get('/test-viewer',headers=headers)
        self.assertEqual(result.json, dict(address='198.51.100.20', scheme='https', host='beta.example'))
        ipv6 = client.get('/test-viewer', headers={**headers,'CloudFront-Viewer-Address':'[2001:db8::1]:1234'})
        self.assertEqual(ipv6.json['address'],'2001:db8::1')
        self.assertEqual(client.get('/analyze.html',headers={**headers,'CloudFront-Forwarded-Proto':'http'}).status_code,403)
        self.assertEqual(client.get('/analyze.html',headers={**headers,'Host':'evil.example'}).status_code,400)
        self.assertEqual(client.get('/analyze.html',headers={**headers,'CloudFront-Viewer-Address':'garbage'}).status_code,403)

    def test_s3_original_workflow_and_restore(self):
        from hosted.app import connect
        from hosted.manage import backup, restore
        fake = self.use_s3()
        p = self.upload()
        directory = self.root/'videos'/p['sessionId']
        self.assertFalse((directory/'source.mp4').exists())
        self.assertEqual(list(fake.objects.values()),[self.video])
        self.assertEqual(self.alice.get('/api/frame?dir='+p['dirName']).status_code,200)
        self.assertEqual(self.bob.get('/api/frame?dir='+p['dirName']).status_code,404)
        self.assertNotIn('bucket',self.alice.get('/api/pitches').text)
        self.assertNotIn('bucket',self.alice.get('/api/clip?dir='+p['dirName']).text)
        self.assertEqual(self.post(self.bob,self.b,'/api/delete-session',dict(sessionId=p['sessionId'])).status_code,404)
        archive=Path(self.tmp.name)/'s3-backup.tar.gz'
        backup(self.root,archive)
        recovered=Path(self.tmp.name)/'s3-restored'
        restore(archive,recovered)
        with connect(recovered) as db:
            self.assertEqual(db.execute('SELECT state FROM s3_objects').fetchone()[0],'active')
        response=self.post(self.alice,self.a,'/api/delete-session',dict(sessionId=p['sessionId']))
        self.assertEqual(response.status_code,200)
        self.assertFalse(response.json['storageDeletionPending'])
        self.assertEqual(fake.objects,{})

    def test_s3_failed_upload_and_failed_registration_are_cleaned(self):
        from hosted.app import connect, cleanup, media_lock
        fake=self.use_s3()
        fake.fail_put=True
        response=self.alice.post('/api/upload?filename=test.mp4',data=self.video,headers={'X-CSRF-Token':self.a})
        self.assertEqual(response.status_code,503)
        self.assertEqual(self.alice.get('/api/pitches').json,{'pitches':[]})
        self.assertEqual(list((self.root/'videos').iterdir()),[])
        self.assertEqual(len(fake.objects),1)
        with media_lock(self.root): cleanup(self.root)
        self.assertEqual(fake.objects,{})
        fake.fail_put=False
        with connect(self.root) as db:
            db.execute("CREATE TRIGGER fail_registration BEFORE INSERT ON videos BEGIN SELECT RAISE(ABORT,'simulated failure'); END")
        response=self.alice.post('/api/upload?filename=test.mp4',data=self.video,headers={'X-CSRF-Token':self.a})
        self.assertEqual(response.status_code,500)
        with media_lock(self.root): cleanup(self.root)
        self.assertEqual(fake.objects,{})

    def test_s3_deletion_retry_and_expiry(self):
        from hosted.app import connect, cleanup, media_lock, create_app
        fake=self.use_s3()
        p=self.upload()
        fake.fail_delete=True
        response=self.post(self.alice,self.a,'/api/delete-session',dict(sessionId=p['sessionId']))
        self.assertTrue(response.json['storageDeletionPending'])
        self.assertEqual(self.alice.get('/api/frame?dir='+p['dirName']).status_code,404)
        self.assertEqual(len(fake.objects),1)
        # A restarted/offline cleanup with no S3 env still has durable bucket/key/region.
        create_app(self.config)
        fake.fail_delete=False
        with media_lock(self.root): cleanup(self.root)
        self.assertEqual(fake.objects,{})
        p=self.upload()
        with connect(self.root) as db:
            db.execute('UPDATE videos SET created=?',(time.time()-8*86400,))
        with media_lock(self.root): cleanup(self.root)
        self.assertEqual(fake.objects,{})
        self.assertFalse((self.root/'videos'/p['sessionId']).exists())

    def test_s3_config_and_versioned_bucket_rejected(self):
        from hosted.app import create_app
        with self.assertRaises(RuntimeError):
            create_app(dict(self.config,S3_BUCKET='my-bucket',S3_REGION=''))
        fake=self.use_s3()
        for status in ('Enabled','Suspended'):
            fake.versioning={'Status':status}
            response=self.alice.post('/api/upload?filename=test.mp4',data=self.video,headers={'X-CSRF-Token':self.a})
            self.assertEqual(response.status_code,503)
        self.assertEqual(fake.objects,{})

    def test_s3_sdk_contract_and_interrupted_registration(self):
        import boto3
        from botocore.stub import Stubber, ANY
        from hosted.app import connect, cleanup, media_lock
        from hosted.storage import store_original
        s3=boto3.client('s3',region_name='us-east-2',aws_access_key_id='test',aws_secret_access_key='test')
        stub=Stubber(s3)
        video='a'*32
        key='armangle/originals/'+video+'/source.mp4'
        stub.add_response('get_bucket_versioning',{}, {'Bucket':'armangle-test-only'})
        stub.add_response('put_object',{'ETag':'"test"'},dict(Bucket='armangle-test-only',Key=key,
                          Body=ANY,ContentLength=len(self.video),ContentType='video/mp4',
                          ServerSideEncryption='AES256',Metadata=ANY))
        stub.add_response('delete_object',{},dict(Bucket='armangle-test-only',Key=key))
        with stub, patch('hosted.storage.client',return_value=s3):
            original=store_original(self.root,video,Path(self.tmp.name)/'fixture.mp4','armangle-test-only','us-east-2')
            self.assertEqual(original['bytes'],len(self.video))
            with connect(self.root) as db:
                self.assertEqual(db.execute('SELECT state FROM s3_objects').fetchone()[0],'pending')
            # Simulate process death before the video is registered. Next cleanup
            # must remove the private object rather than leave it orphaned.
            with media_lock(self.root): cleanup(self.root)
            with connect(self.root) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM s3_objects').fetchone()[0],0)
        stub.assert_no_pending_responses()

    def test_real_1080p_60fps_upload_and_frame(self):
        import cv2
        import numpy as np
        video=Path(self.tmp.name)/'1080p60.mp4'
        writer=cv2.VideoWriter(str(video),cv2.VideoWriter_fourcc(*'mp4v'),60,(1920,1080))
        self.assertTrue(writer.isOpened())
        for i in range(60):
            writer.write(np.full((1080,1920,3),i,dtype=np.uint8))
        writer.release()
        self.video=video.read_bytes()
        p=self.upload()
        self.assertEqual((p['imgW'],p['imgH'],p['nFrames']),(1920,1080,60))
        self.assertAlmostEqual(p['fps'],60)
        image=self.alice.get('/api/frame?dir='+p['dirName']+'&frame=59')
        frame=cv2.imdecode(np.frombuffer(image.data,np.uint8),cv2.IMREAD_COLOR)
        self.assertEqual(frame.shape[:2],(1080,1920))


    def install_example(self, slug='mlb-example', angle=38.2, replace=False):
        from hosted import example
        source = Path(self.tmp.name) / 'example.mp4'
        source.write_bytes(self.video)
        return example.install(self.root, source, dict(sessionName='MLB example', sourceName='example.mp4',
                               pitcherName='Scherzer, Max', pThrows='R', park='TOR', armAngle=angle), slug, replace)

    def test_example_is_offered_only_when_installed(self):
        self.assertNotIn('data-example', self.alice.get('/analyze.html').text)
        self.assertEqual(self.post(self.alice,self.a,'/api/example',{}).status_code, 404)
        self.install_example()
        # The page advertises the clip, so the tile is shown without a second request.
        self.assertIn('data-example="true"', self.alice.get('/analyze.html').text)

    def test_example_clone_is_private_shared_on_disk_and_free(self):
        from hosted.app import connect
        self.install_example()
        first = self.post(self.alice,self.a,'/api/example',{})
        self.assertEqual(first.status_code, 200, first.text)
        pitch = first.json['pitch']
        self.assertFalse(first.json['reused'])
        self.assertTrue(pitch['isLocal'] and pitch['uiTestBatch'])
        self.assertEqual((pitch['gamePk'], pitch['armAngle'], pitch['pitcherName']), ('local', 38.2, 'Scherzer, Max'))
        self.assertEqual(pitch['startFrame'], pitch['nFrames'] // 2)
        # Asking again reopens the same practice pitch instead of piling up copies.
        again = self.post(self.alice,self.a,'/api/example',{})
        self.assertEqual((again.json['reused'], again.json['pitch']['playId']), (True, pitch['playId']))
        self.assertEqual(len(self.alice.get('/api/pitches').json['pitches']), 1)
        # A second visitor gets a pitch of their own and cannot reach this one.
        other = self.post(self.bob,self.b,'/api/example',{}).json['pitch']
        self.assertNotEqual(other['playId'], pitch['playId'])
        self.assertEqual(self.bob.get('/api/frame?dir='+pitch['dirName']).status_code, 404)
        self.assertEqual(self.alice.get('/api/frame?dir='+pitch['dirName']+'&frame=2').status_code, 200)
        self.assertEqual(self.alice.get('/api/clip?dir='+pitch['dirName']).json['nFrames'], 12)
        # Both copies share the installed clip's inodes and cost no storage budget.
        shared = self.root/'examples'/'mlb-example'/'0000.jpg'
        self.assertGreaterEqual(shared.stat().st_nlink, 3)
        for owner in (pitch, other):
            self.assertEqual((self.root/'videos'/owner['sessionId']/'0000.jpg').stat().st_ino, shared.stat().st_ino)
        with connect(self.root) as db:
            self.assertEqual(db.execute('SELECT COALESCE(SUM(bytes),0) FROM videos').fetchone()[0], 0)

    def test_example_keeps_statcast_reference_out_of_the_browser(self):
        self.install_example()
        pitch = self.post(self.alice,self.a,'/api/example',{}).json['pitch']
        forged = dict(self.row(pitch), true_arm_angle=-80, park='NOWHERE')
        self.assertEqual(self.post(self.alice,self.a,'/api/label',forged).status_code, 200)
        saved = self.alice.get('/api/labels').json['labels'][0]
        self.assertEqual((float(saved['true_arm_angle']), saved['park']), (38.2, 'TOR'))
        self.assertEqual(self.alice.get('/api/export?session='+pitch['sessionId']).status_code, 200)

    def test_example_clone_expires_but_the_installed_clip_survives(self):
        from hosted.app import cleanup, connect, media_lock
        self.install_example()
        pitch = self.post(self.alice,self.a,'/api/example',{}).json['pitch']
        with connect(self.root) as db:
            db.execute('UPDATE videos SET created=?', (time.time()-8*86400,))
        with media_lock(self.root):
            cleanup(self.root)
        self.assertFalse((self.root/'videos'/pitch['sessionId']).exists())
        self.assertTrue((self.root/'examples'/'mlb-example'/'0000.jpg').is_file())
        # The visitor can start practising again on a clip retention did not touch.
        self.assertEqual(self.post(self.alice,self.a,'/api/example',{}).status_code, 200)

    def test_example_install_validates_and_replaces(self):
        from hosted import example
        self.install_example()
        self.assertEqual(example.available(self.root), ['mlb-example'])
        with self.assertRaises(ValueError):
            self.install_example()
        pitch, meta = self.install_example(angle=-59.9, replace=True)
        self.assertEqual((pitch['armAngle'], meta['nFrames']), (-59.9, 12))
        with self.assertRaises(ValueError):
            example.directory(self.root, 'Not A Slug')
        self.assertTrue(example.remove(self.root, 'mlb-example'))
        self.assertEqual(example.available(self.root), [])


if __name__=='__main__':
    unittest.main()
