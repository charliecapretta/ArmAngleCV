"""Bounded synthetic 1080p/60fps/30s measurement; rerun on the actual AWS instance."""
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def measure():
    import cv2
    import numpy as np
    import labelServer
    cv2.setNumThreads(1)
    labelServer.getManualAngleBundle()
    with tempfile.TemporaryDirectory(prefix='armangle-capacity-') as tmp:
        root = Path(tmp)
        writer = cv2.VideoWriter(str(root/'source.mp4'), cv2.VideoWriter_fourcc(*'mp4v'), 60, (1920,1080))
        if not writer.isOpened():
            raise RuntimeError('MP4 encoding unavailable')
        frame = np.zeros((1080,1920,3), dtype=np.uint8)
        frame[:,:,0] = np.arange(1920, dtype=np.uint16) % 256
        frame[:,:,1] = np.arange(1080, dtype=np.uint16)[:,None] % 256
        for i in range(1800):
            image = frame.copy()
            cv2.circle(image, (100+i%1000,360), 50, (255,255,255), -1)
            writer.write(image)
        writer.release()
        started = time.monotonic()
        result = subprocess.run([sys.executable,'-m','hosted.video',str(root)],cwd=ROOT,
                                env={**os.environ,'OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1'},
                                capture_output=True,text=True,timeout=110)
        if result.returncode:
            raise RuntimeError(result.stderr)
        scale = 1024**2 if sys.platform=='darwin' else 1024
        return dict(platform=sys.platform, clip='Synthetic 1920x1080, 60 fps, 30 seconds; not representative of all uploads',
                    source_mb=round((root/'source.mp4').stat().st_size/1024**2,2),
                    preparation_seconds=round(time.monotonic()-started,2),
                    decoder_peak_rss_mb=round(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss/scale,2),
                    measurement_process_peak_rss_mb=round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/scale,2),
                    prepared_frame_count=len(list(root.glob('*.jpg'))),
                    total_disk_mb=round(sum(p.stat().st_size for p in root.iterdir())/1024**2,2),
                    limitation='This does not verify Linux service limits or concurrent live-user capacity. Repeat on the actual 1 GB instance.')


if __name__=='__main__':
    print(json.dumps(measure(),indent=2))
