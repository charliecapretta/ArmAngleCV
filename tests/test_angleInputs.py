"""Model-input contracts: resizing, metadata, and failure cases."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import unittest
import numpy as np
from armangle import angleInputs
from armangle import features
class LinearProbe:
    # Scaled to land in the plausible arm-angle range. Predictions are now clamped to
    # [-90, 90], so an unscaled probe (~60000 on this row) would saturate at the rail
    # and these invariance tests would pass trivially without comparing anything.
    def predict(self, x):
        return (x @ np.arange(1,x.shape[1]+1))/1000


class AngleInputsTest(unittest.TestCase):
    def setUp(self):
        self.row={'img_w':1920,'img_h':1080}
        for name,(x,y) in zip(features.KEYPOINT_NAMES,[(1000,600),(900,610),(1060,630),(1120,580),(1150,570)]):
            self.row[name+'_x']=x;self.row[name+'_y']=y

    def resized(self,factor):
        return {k:v*factor for k,v in self.row.items()}

    def test_uniform_resize_has_same_normalized_inputs_and_prediction(self):
        b={'model':LinearProbe(),'features':angleInputs.columns('height720'),'feature_mode':'height720'}
        rows=[self.resized(s) for s in [.25,.5,1,1.5,2,3]]
        p=angleInputs.predict(b,rows)
        np.testing.assert_allclose(p,np.repeat(p[0],len(p)),rtol=0,atol=1e-8)

    def test_720p_input_preserves_legacy_geometry(self):
        r=self.resized(2/3)
        np.testing.assert_allclose(angleInputs.vector(r),features.featureVector(r),atol=1e-12)

    def test_normalization_does_not_mutate_saved_coordinates(self):
        before=dict(self.row);angleInputs.vector(self.row);self.assertEqual(self.row,before)

    def test_unknown_dimensions_and_missing_points_fail_explicitly(self):
        for key,value in [('img_h',0),('img_w',float('nan')),('img_h',''),('wrist_x',float('inf')),('ball_y','')]:
            r={**self.row,key:value}
            with self.assertRaises(ValueError):angleInputs.vector(r)

    def test_legacy_model_has_no_implicit_rescaling(self):
        b={'model':LinearProbe(),'features':features.FEATURE_COLS}
        np.testing.assert_allclose(angleInputs.predict(b,[self.row]),b['model'].predict(np.array([features.featureVector(self.row)])))

    def test_model_schema_cannot_silently_change(self):
        for b in [{'model':LinearProbe(),'features':['ball_angle'],'feature_mode':'height720'},
                  {'model':LinearProbe(),'features':features.FEATURE_COLS,'feature_mode':'typo'}]:
            with self.assertRaises(ValueError):angleInputs.predict(b,[self.row])


class ClampTest(unittest.TestCase):
    """An arm angle cannot leave [-90, 90]; the Ridge is unbounded and can."""

    def setUp(self):
        self.row={'img_w':1920,'img_h':1080}
        for name,(x,y) in zip(features.KEYPOINT_NAMES,[(1000,600),(900,610),(1060,630),(1120,580),(1150,570)]):
            self.row[name+'_x']=x;self.row[name+'_y']=y

    def bundle(self,value):
        class Constant:
            def predict(self,x):return np.full(len(x),value,dtype=float)
        return {'model':Constant(),'features':angleInputs.columns('height720'),'feature_mode':'height720'}

    def test_clamps_both_rails(self):
        self.assertEqual(angleInputs.predict(self.bundle(-137.0),[self.row])[0],-90.0)
        self.assertEqual(angleInputs.predict(self.bundle(210.0),[self.row])[0],90.0)

    def test_leaves_plausible_angles_untouched(self):
        for value in (-64.8,0.0,74.3,-90.0,90.0):
            self.assertAlmostEqual(angleInputs.predict(self.bundle(value),[self.row])[0],value)

    def test_clamping_is_reported_not_silent(self):
        import contextlib,io as _io
        captured=_io.StringIO()
        with contextlib.redirect_stderr(captured):
            angleInputs.predict(self.bundle(-137.0),[self.row])
        self.assertIn('clamped 1',captured.getvalue())

    def test_features_clampAngle_matches(self):
        self.assertEqual(features.clampAngle(-137.0),-90.0)
        self.assertEqual(features.clampAngle(210.0),90.0)
        self.assertEqual(features.clampAngle(12.5),12.5)


if __name__=='__main__':unittest.main()
