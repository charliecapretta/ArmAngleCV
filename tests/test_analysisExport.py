import csv
import io
from pathlib import Path
import unittest
from joblib import load

import analysisExport
import angleInputs

ROOT = Path(__file__).resolve().parents[1]


class AnalysisExportTest(unittest.TestCase):
    def setUp(self):
        self.bundle = load(ROOT / angleInputs.DEFAULT_MODEL)
        with (ROOT / 'data/manual-v1/labels.csv').open(newline='') as handle:
            self.row = next(row for row in csv.DictReader(handle) if row['status'] == 'labeled')
        self.pitch = dict(gamePk=self.row['game_pk'], playId=self.row['play_id'], sessionId='s1',
                          sourceName='video, one.mp4', sessionName='@session', pitcherName='Name')

    def rows(self, labels, pitches=None):
        return list(csv.DictReader(io.StringIO(analysisExport.exportCsv(labels, pitches or [self.pitch], self.bundle, 's1'))))

    def test_uses_saved_coordinates_and_matches_model(self):
        row = dict(self.row, predicted_arm_angle=999)
        result = self.rows([row])[0]
        self.assertAlmostEqual(float(result['predicted_arm_angle_deg']), angleInputs.predict(self.bundle, [row])[0])
        self.assertEqual(result['session_name'], "'@session")
        self.assertEqual(result['video_name'], 'video, one.mp4')
        self.assertEqual(result['model_version'], 'manual-v1')
        self.assertEqual(result['release_frame'], row['frame_index'])

    def test_legacy_incomplete_and_occluded_rows_are_retained_without_predictions(self):
        for status in ['labeled', 'occluded', 'release_selected', 'skipped']:
            result = self.rows([dict(self.row, status=status, ball_x='')])[0]
            self.assertEqual(result['predicted_arm_angle_deg'], '')
            self.assertEqual(result['measured_angle_deg'], '')
            self.assertEqual(result['status'], 'incomplete' if status == 'labeled' else status)

    def test_session_boundary_and_empty_export(self):
        other = dict(self.row, play_id='other')
        result = self.rows([self.row, other], [self.pitch, dict(self.pitch, playId='other', sessionId='s2')])
        self.assertEqual(len(result), 1)
        with self.assertRaisesRegex(ValueError, 'Save at least'):
            self.rows([])

    def test_old_reel_registrations_share_a_stable_session(self):
        a = dict(sourcePath='reel.mp4', playId='one')
        b = dict(sourcePath='reel.mp4', playId='two')
        self.assertEqual(analysisExport.sessionId(a), analysisExport.sessionId(b))
        self.assertNotEqual(analysisExport.sessionId(a), analysisExport.sessionId(dict(b, sourcePath='different.mp4')))


if __name__ == '__main__':
    unittest.main()
