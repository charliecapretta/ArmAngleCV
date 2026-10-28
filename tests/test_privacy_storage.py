"""Privacy regressions using real uploads and snapshots, isolated from user data."""
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_hosted


class PrivacyStorageTest(unittest.TestCase):
    setUp = test_hosted.HostedTest.setUp
    tearDown = test_hosted.HostedTest.tearDown
    login = test_hosted.HostedTest.login
    upload = test_hosted.HostedTest.upload
    post = test_hosted.HostedTest.post
    use_s3 = test_hosted.HostedTest.use_s3

    def test_multipart_upload_discards_original_and_recovers_frames(self):
        from hosted.app import connect, create_app
        from hosted.manage import backup, restore
        from hosted.deletions import reconcile
        self.app.config['RETAIN_ORIGINALS'] = False
        # A valid padding atom crosses Werkzeug's parser chunk size. Tiny
        # fixtures alone would miss a form-memory limit that rejects real clips.
        video = self.video + (200000).to_bytes(4, 'big') + b'free' + bytes(199992)
        response = self.alice.post('/api/upload', data={
            'video': (io.BytesIO(video), 'private name.mp4'),
            'pitcherName': 'Private Player', 'sessionName': 'Private Session', 'pThrows': 'L',
        }, headers={'X-CSRF-Token': self.a})
        self.assertEqual(response.status_code, 200, response.text)
        pitch = response.json['pitch']
        self.assertEqual(pitch['pitcherName'], 'Private Player')
        self.assertEqual(pitch['sessionName'], 'Private Session')
        directory = self.root / 'videos' / pitch['sessionId']
        self.assertFalse((directory / 'source.mp4').exists())
        with connect(self.root) as db:
            row = db.execute('SELECT * FROM videos').fetchone()
            self.assertEqual(json.loads(row['metadata'])['original']['kind'], 'discarded')
            self.assertEqual(row['bytes'], sum(p.stat().st_size for p in directory.iterdir()))
        archive = Path(self.tmp.name) / 'frames.tar.gz'
        backup(self.root, archive)
        recovered = Path(self.tmp.name) / 'frames-restored'
        restore(archive, recovered)
        reconcile(recovered, current_data=self.root)
        client = create_app(dict(self.config, DATA_DIR=str(recovered))).test_client()
        self.login(client, 'alice')
        with client.get('/api/frame?dir=' + pitch['dirName']) as frame:
            self.assertEqual(frame.status_code, 200)
            self.assertIn('no-store', frame.headers['Cache-Control'])

    def test_restore_cannot_resurrect_deleted_session(self):
        from hosted.app import create_app, connect
        from hosted.manage import backup, restore
        from hosted.deletions import reconcile
        deleted = self.upload()
        surviving = self.upload()
        archive = Path(self.tmp.name) / 'before-delete.tar.gz'
        backup(self.root, archive)
        self.assertEqual(self.post(self.alice, self.a, '/api/delete-session',
                                  {'sessionId': deleted['sessionId']}).status_code, 200)
        recovered = Path(self.tmp.name) / 'restored'
        restore(archive, recovered)
        config = dict(self.config, DATA_DIR=str(recovered))
        with self.assertRaisesRegex(RuntimeError, 'quarantined'):
            create_app(config)
        with self.assertRaises(ValueError):
            reconcile(recovered, current_data=recovered)
        self.assertTrue((recovered / 'restore.pending').exists())
        reconcile(recovered, current_data=self.root)
        client = create_app(config).test_client()
        self.login(client, 'alice')
        self.assertEqual(client.get('/api/frame?dir=' + deleted['dirName']).status_code, 404)
        with client.get('/api/frame?dir=' + surviving['dirName']) as response:
            self.assertEqual(response.status_code, 200)
        with connect(recovered) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM videos').fetchone()[0], 1)
        self.assertFalse((recovered / 'videos' / deleted['sessionId']).exists())

    def test_delete_all_is_owned_csrf_protected_and_ends_session(self):
        from hosted.app import connect
        first = self.upload()
        second = self.upload()
        with connect(self.root) as db:
            db.execute("UPDATE videos SET owner='bob' WHERE id=?", (second['sessionId'],))
        self.assertEqual(self.alice.post('/api/delete-all', json={}).status_code, 403)
        response = self.post(self.alice, self.a, '/api/delete-all', {})
        self.assertEqual(response.status_code, 200)
        self.assertIn('cache', response.headers['Clear-Site-Data'])
        self.assertEqual(self.alice.get('/api/pitches').status_code, 401)
        self.assertFalse((self.root / 'videos' / first['sessionId']).exists())
        with self.bob.get('/api/frame?dir=' + second['dirName']) as frame:
            self.assertEqual(frame.status_code, 200)

    def test_remote_ledger_survives_snapshot_and_failures_stay_quarantined(self):
        from hosted.manage import backup, restore
        from hosted.deletions import reconcile, PREFIX
        fake = self.use_s3()
        self.app.config.update(DELETION_BUCKET='deletion-test', DELETION_REGION='us-east-2')
        from hosted.deletions import configure
        configure(self.root, 'deletion-test', 'us-east-2')
        pitch = self.upload()
        archive = Path(self.tmp.name) / 'remote.tar.gz'
        backup(self.root, archive)
        response = self.post(self.alice, self.a, '/api/delete-session', {'sessionId': pitch['sessionId']})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(fake.objects[('deletion-test', PREFIX + pitch['sessionId'])], b'1')
        recovered = Path(self.tmp.name) / 'remote-restored'
        restore(archive, recovered)
        with self.assertRaisesRegex(ValueError, 'bound to this snapshot'):
            reconcile(recovered, bucket='wrong-bucket', region='us-east-2')
        fake.list_objects_v2 = lambda **kwargs: (_ for _ in ()).throw(OSError('unavailable'))
        with self.assertRaises(OSError):
            reconcile(recovered, bucket='deletion-test', region='us-east-2')
        self.assertTrue((recovered / 'restore.pending').exists())
        fake.list_objects_v2 = lambda **kwargs: {'Contents': [{'Key': PREFIX + pitch['sessionId']}]}
        reconcile(recovered, bucket='deletion-test', region='us-east-2')
        self.assertFalse((recovered / 'videos' / pitch['sessionId']).exists())

    def test_failed_deletion_ledger_write_does_not_claim_success(self):
        fake = self.use_s3()
        pitch = self.upload()
        self.app.config.update(DELETION_BUCKET='deletion-test', DELETION_REGION='us-east-2')
        fake.fail_put = True
        response = self.post(self.alice, self.a, '/api/delete-session', {'sessionId': pitch['sessionId']})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('ok', response.json)
        fake.fail_put = False
        self.assertEqual(self.post(self.alice, self.a, '/api/delete-session',
                                  {'sessionId': pitch['sessionId']}).status_code, 200)

    def test_multipart_file_limit_and_public_configuration(self):
        from hosted.app import create_app
        with patch('hosted.app.MAX_BYTES', 4), patch('hosted.app.subprocess.run') as decode:
            response = self.alice.post('/api/upload', data={'video': (io.BytesIO(b'12345'), 'big.mp4')},
                                       headers={'X-CSRF-Token': self.a})
        self.assertEqual(response.status_code, 413)
        decode.assert_not_called()
        with self.assertRaisesRegex(RuntimeError, 'Public release requires'):
            create_app(dict(self.config, PUBLIC_RELEASE=True))

    def test_untrusted_recovery_can_discard_sessions(self):
        from hosted.manage import backup, restore
        from hosted.deletions import reconcile
        from hosted.app import connect
        self.upload()
        archive = Path(self.tmp.name) / 'discard.tar.gz'
        backup(self.root, archive)
        recovered = Path(self.tmp.name) / 'discard-restored'
        restore(archive, recovered)
        reconcile(recovered, discard=True)
        with connect(recovered) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM videos').fetchone()[0], 0)
        self.assertEqual(list((recovered / 'videos').iterdir()), [])

    def test_backup_retention_cannot_be_extended_past_notice(self):
        from hosted.backups import prune_backups
        for days in (0, -1, 8):
            with self.assertRaises(ValueError):
                prune_backups('test-bucket', 'us-east-2', days)
