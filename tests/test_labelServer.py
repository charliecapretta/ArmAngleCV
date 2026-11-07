"""Data-integrity checks: all writes use temporary files, never project labels."""
import csv
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from armangle import labelServer as server
import extractReleaseFrames


class LabelsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patches = [
            patch.object(server, 'LABELS_PATH', str(self.root / 'labels.csv')),
            patch.object(server, 'LOCAL_LABELS_PATH', str(self.root / 'localLabels.csv')),
            patch.object(server, 'ANALYSIS_LABELS_PATH', str(self.root / 'analysisLabels.csv')),
            patch.object(server, 'LOCAL_VIDEOS_PATH', str(self.root / 'localVideos.json')),
            patch.object(server, 'getClip', return_value={'jpegs': [b'jpeg']*4, 'frameTimes':[0,.033,.068,.1], 'fps':30, 'imgW':1280, 'imgH':720}),
            patch.object(server, 'clipPathFor', return_value='/original/reel.mp4'),
            patch.object(server, 'pitcherFor', return_value=('', 'Test pitcher')),
        ]
        for p in self.patches: p.start()
        server.undoRecords.clear()

    def tearDown(self):
        for p in reversed(self.patches): p.stop()
        self.temp.cleanup()

    def row(self, status='labeled', game='123'):
        r = {'game_pk':game, 'play_id':'pitch', 'frame_index':2, 'status':status}
        for name, x in [('shoulder',100),('off_shoulder',120),('elbow',150),('wrist',200),('ball',220)]:
            r[name+'_x'] = x; r[name+'_y'] = 200
        return r

    def test_release_only_is_persistent_and_exportable(self):
        server.writeLabel(self.row('release_selected'))
        r = server.readLabels()[0]
        self.assertEqual(r['status'], 'release_selected')
        self.assertEqual(r['release_time_sec'], '0.068')
        self.assertEqual(r['ball_x'], '')
        self.assertEqual(r['shoulder_x'], '')
        self.assertEqual(len(extractReleaseFrames.readLabels(server.LABELS_PATH)), 1)

    def test_local_labels_never_enter_mlb_file(self):
        server.writeLabel(self.row('labeled'))
        before = Path(server.LABELS_PATH).read_bytes()
        server.writeLabel(self.row('release_selected', 'local'))
        self.assertEqual(Path(server.LABELS_PATH).read_bytes(), before)
        self.assertEqual(len(server.readLabels()), 2)
        self.assertEqual(len(server.readLabelFile(server.LOCAL_LABELS_PATH)), 1)

    def test_analysis_edits_and_undo_preserve_training_and_local_originals(self):
        for game in ['123', 'local']:
            server.writeLabel(self.row(game=game))
        before = {p: Path(p).read_bytes() for p in [server.LABELS_PATH, server.LOCAL_LABELS_PATH]}
        for game in ['123', 'local']:
            r = self.row(game=game); r.update(workspace='analysis', frame_index=3)
            _, token = server.writeLabel(r, withUndo=True)
            analysis = {(r['game_pk'], r['play_id']): r for r in server.readLabels('analysis')}
            self.assertEqual(analysis[(game, 'pitch')]['frame_index'], '3')
            self.assertEqual(len(server.readLabels('training')), 1)
            server.undoLabel(token)
            self.assertEqual(server.readLabelFile(server.ANALYSIS_LABELS_PATH), [])
        for p, original in before.items():
            self.assertEqual(Path(p).read_bytes(), original)
        self.assertEqual(server.readLabels('analysis')[0]['frame_index'], '2')

    def test_training_rejects_local_footage_and_unknown_workspace(self):
        for game, workspace in [('local', 'training'), ('123', 'typo')]:
            r = self.row(game=game); r['workspace'] = workspace
            with self.assertRaises(ValueError): server.writeLabel(r)
        self.assertFalse(Path(server.LABELS_PATH).exists())

    def test_occluded_preserves_body_without_fabricated_ball(self):
        r = self.row('occluded'); r.pop('ball_x'); r.pop('ball_y')
        server.writeLabel(r)
        saved = server.readLabels()[0]
        self.assertEqual(saved['ball_visibility'], 'occluded')
        self.assertEqual(saved['ball_angle'], '')
        self.assertNotEqual(saved['wrist_angle'], '')

    def test_incomplete_or_invalid_points_cannot_be_labeled(self):
        for key, value in [('ball_x',''),('wrist_y',float('nan')),('elbow_x',1280)]:
            r = self.row(); r[key] = value
            with self.assertRaises(ValueError): server.writeLabel(r)
        self.assertFalse(Path(server.LABELS_PATH).exists())

    def test_invalid_frame_cannot_change_existing_label(self):
        server.writeLabel(self.row())
        before = Path(server.LABELS_PATH).read_bytes()
        for frame in [-1,4,1.5]:
            r = self.row(); r['frame_index'] = frame
            with self.assertRaises(ValueError): server.writeLabel(r)
        self.assertEqual(Path(server.LABELS_PATH).read_bytes(), before)

    def test_failed_atomic_write_keeps_previous_labels(self):
        server.writeLabel(self.row())
        before = Path(server.LABELS_PATH).read_bytes()
        with patch.object(server.os, 'replace', side_effect=OSError('disk full')):
            with self.assertRaises(OSError): server.writeLabel(self.row('release_selected'))
        self.assertEqual(Path(server.LABELS_PATH).read_bytes(), before)
        self.assertFalse(list(self.root.glob('.label-*')))

    def test_quality_flags_round_trip_and_do_not_claim_certainty(self):
        r = self.row(); r['release_quality'] = 'unclear'; r['point_quality'] = 'uncertain'
        server.writeLabel(r)
        saved = server.readLabels()[0]
        self.assertEqual(saved['release_quality'], 'unclear')
        self.assertEqual(saved['point_quality'], 'uncertain')
        r['release_quality'] = 'exact'
        with self.assertRaises(ValueError): server.writeLabel(r)

    def test_manual_prediction_is_read_only_and_uses_source_dimensions(self):
        server.writeLabel(self.row())
        before = Path(server.LABELS_PATH).read_bytes()
        r = self.row(); r['img_h'] = 9999
        with patch.object(server, 'manualAngleBundle', {'feature_mode':'height720'}), \
             patch('armangle.angleInputs.predict', return_value=[12.5]) as prediction:
            result = server.predictManualAngle(r)
            self.assertEqual(result['predicted_arm_angle'], 12.5)
            self.assertEqual(prediction.call_args.args[1][0]['img_h'], 720)
        self.assertEqual(Path(server.LABELS_PATH).read_bytes(), before)

    def test_undo_first_save_really_removes_row(self):
        _, token = server.writeLabel(self.row('release_selected'), withUndo=True)
        server.undoLabel(token)
        self.assertEqual(server.readLabels(), [])

    def test_undo_restores_legacy_incomplete_row_without_relabeling(self):
        old = self.row(); old['ball_x'] = old['ball_y'] = ''
        server.writeRows(server.LABELS_PATH, [old])
        _, token = server.writeLabel(self.row('release_selected'), withUndo=True)
        server.undoLabel(token)
        restored = server.readLabels()[0]
        self.assertEqual(restored['status'], 'labeled')
        self.assertEqual(restored['shoulder_x'], '100')
        self.assertEqual(restored['ball_x'], '')

    def test_undo_refuses_to_overwrite_later_changes(self):
        _, oldToken = server.writeLabel(self.row('release_selected'), withUndo=True)
        server.writeLabel(self.row())
        with self.assertRaises(ValueError): server.undoLabel(oldToken)
        self.assertEqual(server.readLabels()[0]['status'], 'labeled')


if __name__ == '__main__': unittest.main()
