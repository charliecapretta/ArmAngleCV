"""Exercise a fresh local server through HTTP with a real, temporary video.

All registrations, labels, and exports are in a temporary checkout. Optional
download/pose packages are explicitly blocked in the server process.
"""
import csv
import io
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]


class FirstRunTest(unittest.TestCase):
    def test_manual_http_workflow_needs_no_optional_packages(self):
        import cv2
        import numpy as np

        with tempfile.TemporaryDirectory(prefix='armangle-smoke-') as tmp:
            root = Path(tmp)
            for name in ['labelServer.py', 'savantUtils.py', 'features.py', 'angleInputs.py',
                         'scripts/predictLabels.py', 'analysisExport.py', 'videoUploads.py', 'model/armAngleModelResolutionSafe.joblib',
                         'index.html', 'labelTool.html', 'labelTool.js', 'workspace.css']:
                (root/name).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT/name, root/name)
            (root/'sitecustomize.py').write_text('''import importlib.abc, sys
class BlockOptional(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname.split('.')[0] in {'requests','bs4','torch','torchvision','ultralytics','baseballcv'}:
            raise ModuleNotFoundError('Optional package loaded during manual workflow: '+fullname)
sys.meta_path.insert(0, BlockOptional())
''')
            video = root/'test-clip.mp4'
            writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*'mp4v'), 30, (320, 180))
            self.assertTrue(writer.isOpened(), 'The supported environment must encode an MP4 fixture')
            for i in range(12):
                frame = np.zeros((180, 320, 3), dtype=np.uint8)
                cv2.circle(frame, (100+i, 80), 5, (255, 255, 255), -1)
                writer.write(frame)
            writer.release()
            original = b'game_pk,play_id,status\n123,training-record,skipped\n'
            (root/'data').mkdir()
            (root/'data'/'labels.csv').write_bytes(original)
            env = {**os.environ, 'PYTHONPATH': str(root), 'PYTHONUNBUFFERED': '1'}
            logPath = root/'server.log'
            with logPath.open('w+') as log:
                process = subprocess.Popen([sys.executable, str(root/'labelServer.py'), '0'],
                                           cwd=root, env=env, stdout=subprocess.PIPE,
                                           stderr=log, text=True)
                lines = queue.Queue()
                def readOutput():
                    for line in process.stdout:
                        lines.put(line)
                reader = threading.Thread(target=readOutput, daemon=True)
                reader.start()
                try:
                    base = None
                    deadline = time.monotonic()+30
                    while time.monotonic() < deadline and process.poll() is None:
                        try:
                            line = lines.get(timeout=.1)
                        except queue.Empty:
                            continue
                        match = re.search(r'(http://127\.0\.0\.1:\d+)/index.html', line)
                        if match:
                            base = match.group(1)
                            break
                    self.assertIsNotNone(base, 'Server did not start: '+logPath.read_text())

                    def request(path, payload=None):
                        data = None if payload is None else json.dumps(payload).encode()
                        req = Request(base+path, data=data, headers={'Content-Type': 'application/json'})
                        try:
                            with urlopen(req, timeout=45) as response:
                                body = response.read()
                                return json.loads(body) if 'application/json' in response.headers['Content-Type'] else body
                        except Exception as error:
                            raise AssertionError(f'HTTP {path}: {error}\n{logPath.read_text()}') from error

                    self.assertIn(b'data-workspace="analysis"', request('/index.html'))
                    self.assertIn(b'data-workspace="analysis"', request('/analyze.html'))
                    self.assertEqual(request('/api/pitches?workspace=analysis')['pitches'], [])
                    metadata = urlencode({'filename': 'test-clip.mp4', 'pThrows': 'R',
                                          'pitcherName': '=Smoke test', 'sessionName': 'Bullpen, day 1'})
                    with urlopen(Request(base+'/api/upload?'+metadata, data=video.read_bytes(),
                                         headers={'Content-Type': 'video/mp4'}), timeout=45) as response:
                        pitch = json.load(response)['pitch']
                    self.assertEqual(pitch['sessionName'], 'Bullpen, day 1')
                    self.assertNotEqual(pitch['sourcePath'], str(video))
                    self.assertEqual(Path(pitch['sourcePath']).read_bytes(), video.read_bytes())
                    # Invalid video data never creates a pitch or leaves an upload behind.
                    with self.assertRaises(HTTPError) as rejected:
                        urlopen(Request(base+'/api/upload?filename=bad.mp4', data=b'not a video'), timeout=10)
                    self.assertEqual(rejected.exception.code, 400)
                    rejected.exception.close()
                    self.assertEqual(len(list((root/'uploads').iterdir())), 1)
                    self.assertEqual(len(request('/api/pitches?workspace=analysis')['pitches']), 1)
                    clip = request('/api/clip?dir='+pitch['dirName'])
                    self.assertEqual(clip['nFrames'], 12)
                    self.assertEqual(len(clip['frameTimes']), 12)
                    self.assertTrue(request('/api/frame?dir='+pitch['dirName']+'&frame=3').startswith(b'\xff\xd8'))
                    row = {'game_pk': 'local', 'play_id': pitch['playId'], 'frame_index': 3,
                           'status': 'release_selected', 'workspace': 'analysis'}
                    request('/api/label', row)
                    saved = request('/api/labels?workspace=analysis')['labels'][0]
                    self.assertEqual(saved['status'], 'release_selected')
                    self.assertEqual(saved['ball_x'], '')
                    row['status'] = 'labeled'
                    for name, xy in zip(['shoulder','off_shoulder','elbow','wrist','ball'],
                                        [(145,85),(130,88),(160,80),(175,73),(180,70)]):
                        row[name+'_x'], row[name+'_y'] = xy
                    prediction = request('/api/predict-angle', row)
                    self.assertEqual(prediction['feature_mode'], 'height720')
                    self.assertTrue(-90 <= prediction['predicted_arm_angle'] <= 90)
                    token = request('/api/label', row)['undoToken']
                    saved = request('/api/labels?workspace=analysis')['labels'][0]
                    self.assertEqual(saved['status'], 'labeled')
                    self.assertEqual(saved['img_h'], '180')
                    exported = subprocess.run([sys.executable, str(root/'scripts'/'predictLabels.py'),
                                               '--labels', str(root/'analysisLabels.csv'),
                                               '--out', str(root/'export.csv')],
                                              cwd=root, env=env, capture_output=True, text=True, timeout=30)
                    self.assertEqual(exported.returncode, 0, exported.stderr)
                    exportedRows = list(csv.DictReader(io.StringIO((root/'export.csv').read_text())))
                    self.assertAlmostEqual(float(exportedRows[0]['predicted_arm_angle']), prediction['predicted_arm_angle'])
                    second = request('/api/import', {'parentId': pitch['playId'], 'startFrame': 5})['pitch']
                    third = request('/api/import', {'parentId': pitch['playId'], 'startFrame': 7})['pitch']
                    self.assertEqual(second['sessionId'], pitch['sessionId'])
                    self.assertEqual(third['sourcePath'], pitch['sourcePath'])
                    request('/api/label', dict(row, play_id=second['playId'], frame_index=5, status='occluded'))
                    request('/api/label', dict(row, play_id=third['playId'], frame_index=7, status='release_selected'))
                    def sessionExport():
                        return list(csv.DictReader(io.StringIO(request('/api/export?session='+pitch['sessionId']).decode('utf-8-sig'))))
                    csvRows = sessionExport()
                    self.assertEqual(len(csvRows), 3)
                    self.assertEqual([r['status'] for r in csvRows], ['labeled','occluded','release_selected'])
                    self.assertEqual(csvRows[0]['pitcher_name'], "'=Smoke test")
                    self.assertEqual(csvRows[0]['video_name'], 'test-clip.mp4')
                    self.assertEqual(csvRows[0]['model_version'], 'manual-v1')
                    self.assertIn('has not been established', csvRows[0]['estimate_scope'])
                    self.assertNotIn(str(root), str(csvRows))
                    self.assertAlmostEqual(float(csvRows[0]['predicted_arm_angle_deg']), float(exportedRows[0]['predicted_arm_angle']))
                    self.assertEqual(csvRows[1]['predicted_arm_angle_deg'], '')
                    self.assertEqual(csvRows[2]['measured_angle_deg'], '')
                    # Saved edits recompute; no browser-provided estimate can enter the CSV.
                    changed = dict(row, ball_y=60, predicted_arm_angle=999)
                    changedPrediction = request('/api/predict-angle', changed)['predicted_arm_angle']
                    token = request('/api/label', changed)['undoToken']
                    self.assertAlmostEqual(float(sessionExport()[0]['predicted_arm_angle_deg']), changedPrediction)
                    request('/api/undo', {'undoToken': token})
                    self.assertAlmostEqual(float(sessionExport()[0]['predicted_arm_angle_deg']), prediction['predicted_arm_angle'])
                    # Locking a different release clears saved points and the export estimate.
                    request('/api/label', dict(row, frame_index=4, status='release_selected'))
                    self.assertEqual(sessionExport()[0]['predicted_arm_angle_deg'], '')
                    saved = request('/api/labels?workspace=analysis')['labels']
                    self.assertEqual(len(saved), 3)
                    self.assertEqual((root/'data'/'labels.csv').read_bytes(), original)
                    self.assertFalse((root/'localLabels.csv').exists())
                    self.assertFalse((root/'yolo11n-pose.pt').exists())
                finally:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=10)
                    reader.join(timeout=2)
                    process.stdout.close()
                self.assertNotIn('Traceback', logPath.read_text())


if __name__ == '__main__':
    unittest.main()
