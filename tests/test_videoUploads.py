from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np
import videoUploads


class VideoUploadsTest(unittest.TestCase):
    def test_real_mp4_and_decoded_limits(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'clip.mp4'
            writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'mp4v'), 30, (320, 180))
            self.assertTrue(writer.isOpened())
            for _ in range(12):
                writer.write(np.zeros((180, 320, 3), dtype=np.uint8))
            writer.release()
            info = videoUploads.validateVideo(path)
            self.assertEqual(info['nFrames'], 12)
            for name, limit, message in [('MAX_BYTES', 5, '100 MB'), ('MAX_WIDTH', 100, '1920'),
                                         ('MAX_SECONDS', .1, '60 seconds'), ('MAX_FPS', 20, '60 fps'),
                                         ('MAX_FRAMES', 3, '3,600')]:
                with self.subTest(name=name), patch.object(videoUploads, name, limit):
                    with self.assertRaisesRegex(ValueError, message):
                        videoUploads.validateVideo(path)

    def test_disguised_non_video(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'fake.mp4'
            path.write_bytes(b'pretending to be a video')
            with self.assertRaisesRegex(ValueError, 'not a supported MP4'):
                videoUploads.validateVideo(path)


if __name__ == '__main__':
    unittest.main()
