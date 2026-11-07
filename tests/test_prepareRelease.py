"""The public release is an allowlisted copy, not a copy of the working folder."""
from pathlib import Path
import tempfile
import unittest
import zipfile

from tools.prepareRelease import build


class PublicReleaseTest(unittest.TestCase):
    def test_only_listed_files_are_exported_and_outputs_are_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)/'project'
            root.mkdir()
            (root/'README.md').write_text('Public readme')
            (root/'tools').mkdir()
            (root/'tools/release-files.txt').write_text('README.md\ntools/release-files.txt\n')
            (root/'localVideos.json').write_text('private')
            (root/'.idea').mkdir()
            (root/'.idea/private.xml').write_text('private')
            (root/'.git').mkdir()
            (root/'.git/config').write_text('private')
            out = Path(tmp)/'public'
            count, archive = build(root, out)
            self.assertEqual(count, 3)
            self.assertEqual({p.name for p in out.iterdir()}, {'README.md','tools','RELEASE_CONTENTS.json'})
            with zipfile.ZipFile(archive) as bundle:
                self.assertEqual(set(bundle.namelist()), {'README.md','tools/release-files.txt','RELEASE_CONTENTS.json'})
            with self.assertRaises(ValueError):
                build(root, out)

    def test_private_path_fails_before_output_is_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'tools').mkdir()
            (root/'tools/release-files.txt').write_text('report.txt\n')
            (root/'report.txt').write_text('/Users/'+'private-person/Downloads/reel.mp4')
            out = root/'public'
            with self.assertRaises(ValueError):
                build(root, out)
            self.assertFalse(out.exists())


if __name__ == '__main__':
    unittest.main()
