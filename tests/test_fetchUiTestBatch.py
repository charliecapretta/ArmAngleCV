"""A committed manifest is not proof that a fresh checkout has the videos."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import fetchUiTestBatch as fetch


class RestoreExamplesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.record = {'gamePk': 123, 'playId': 'abc-def', 'uiTestBatch': True}
        self.folder = self.root / 'pitches' / '123_abc-def'
        self.manifest = self.root / 'batch.json'
        self.manifest.write_text(json.dumps({'pitches': [self.record]}))
        self.original = self.manifest.read_bytes()
        self.output = contextlib.redirect_stdout(io.StringIO())
        self.output.__enter__()
        self.addCleanup(self.output.__exit__, None, None, None)

    def valid(self, path):
        return Path(path).exists() and Path(path).read_bytes() == b'valid video'

    def download(self, url, path, session):
        Path(path).write_bytes(b'valid video')
        return True

    def test_missing_video_is_restored_even_when_manifest_is_complete(self):
        with patch.object(fetch, 'usableClip', side_effect=self.valid), \
             patch.object(fetch.s, 'resolveClipUrl', return_value='https://example.test/clip.mp4'), \
             patch.object(fetch.s, 'downloadClip', side_effect=self.download) as download:
            self.assertEqual(fetch.restorePitches(fetch.manifestPitches(self.manifest), self.root/'pitches', object()), [])
        download.assert_called_once()
        self.assertTrue(self.valid(self.folder/'clip.mp4'))
        self.assertEqual(json.loads((self.folder/'meta.json').read_text()), self.record)
        self.assertEqual(self.manifest.read_bytes(), self.original)

    def test_healthy_video_restores_metadata_without_download_dependencies(self):
        self.folder.mkdir(parents=True)
        (self.folder/'clip.mp4').write_bytes(b'valid video')
        with patch.object(fetch, 'usableClip', side_effect=self.valid), \
             patch.object(fetch.s, 'makeSession', side_effect=AssertionError('No network needed')):
            self.assertEqual(fetch.restorePitches([self.record], self.root/'pitches'), [])
        self.assertEqual(json.loads((self.folder/'meta.json').read_text()), self.record)

    def test_existing_metadata_is_preserved(self):
        self.folder.mkdir(parents=True)
        (self.folder/'clip.mp4').write_bytes(b'valid video')
        saved = json.dumps({**self.record, 'note': 'existing local metadata'}).encode()
        (self.folder/'meta.json').write_bytes(saved)
        with patch.object(fetch, 'usableClip', side_effect=self.valid):
            self.assertEqual(fetch.restorePitches([self.record], self.root/'pitches'), [])
        self.assertEqual((self.folder/'meta.json').read_bytes(), saved)

    def test_failed_download_keeps_old_file_and_cleans_partial_files(self):
        self.folder.mkdir(parents=True)
        (self.folder/'clip.mp4').write_bytes(b'old broken video')
        def broken(url, path, session):
            Path(path+'.part').write_bytes(b'partial')
            raise RuntimeError('connection lost')
        with patch.object(fetch, 'usableClip', side_effect=self.valid), \
             patch.object(fetch.s, 'resolveClipUrl', return_value='https://example.test/clip.mp4'), \
             patch.object(fetch.s, 'downloadClip', side_effect=broken):
            self.assertEqual(fetch.restorePitches([self.record], self.root/'pitches', object()), ['123_abc-def'])
        self.assertEqual((self.folder/'clip.mp4').read_bytes(), b'old broken video')
        self.assertEqual([p.name for p in self.folder.iterdir()], ['clip.mp4'])

    def test_manifest_rejects_duplicate_and_unsafe_identifiers(self):
        for records in [[self.record, self.record], [{**self.record, 'playId': '../outside'}], ['not a pitch object']]:
            self.manifest.write_text(json.dumps({'pitches': records}))
            with self.assertRaises(ValueError):
                fetch.manifestPitches(self.manifest)


if __name__ == '__main__':
    unittest.main()
