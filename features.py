"""
The one definition of a feature row, shared by labeling, evaluation and inference.

Extracted so predicted keypoints and hand-clicked keypoints go through byte-for-byte
identical arithmetic. Before this, pixelAngle lived in labelServer.py and FEATURE_COLS
in AnglePredictor.py, which was fine while the only source of coordinates was a human
clicking -- but Phase 4 adds a second source, and two copies of an arctangent is exactly
how a training/inference skew gets in.

Nothing here imports numpy, pandas or torch: it is plain arithmetic on floats so any
stage of the pipeline can use it cheaply.
"""

import math

# The five clicked points, in the order the keypoint model emits them.
# shoulder is the THROWING shoulder (chosen from p_throws), off_shoulder the glove side.
KEYPOINT_NAMES = ["shoulder", "off_shoulder", "elbow", "wrist", "ball"]

COORD_COLS = [f"{name}_{axis}" for name in KEYPOINT_NAMES for axis in ("x", "y")]

# ball_angle absorbs the arctangent; the raw coordinates then correct the residual,
# mostly torso tilt. Measured: ball_angle alone 3.23 RMSE, raw pixels alone 5.04,
# both together 2.65. Do NOT add frame_index, img_w, img_h, venue_id or game_pk.
FEATURE_COLS = [
    'ball_angle', 'wrist_angle',
    'shoulder_x', 'shoulder_y',
    'wrist_x', 'wrist_y',
    'elbow_x', 'elbow_y',
    'off_shoulder_x', 'off_shoulder_y',
    'ball_x', 'ball_y',
]

TARGET_COL = 'true_arm_angle'

# Fold splits MUST be grouped by this. A pitcher's arm angle is nearly constant, so a
# random split leaves 67% of the test set contaminated and flatters the score badly.
GROUP_COL = 'pitcher_id'


def pixelAngle(shoulderX, shoulderY, farX, farY):
    """
    Angle at the shoulder, up from horizontal, in image coordinates.

    y grows downward in an image, hence the negated dy. abs() on dx makes lefties
    and righties directly comparable. Overhead +90, sidearm 0, submarine negative
    -- the sign convention Statcast's arm_angle uses.
    """
    try:
        dx = abs(float(farX) - float(shoulderX))
        dy = -(float(farY) - float(shoulderY))
    except (TypeError, ValueError):
        return ""
    return round(math.degrees(math.atan2(dy, dx)), 3)


def deriveAngles(coords):
    """
    ball_angle and wrist_angle from a dict of pixel coordinates.

    Accepts anything with COORD_COLS keys -- a csv row, or a model's predictions
    mapped back into full-frame pixels. Returns "" for either angle when the
    shoulder or the far point is missing, matching what labels.csv stores.
    """
    shoulderX, shoulderY = coords.get("shoulder_x"), coords.get("shoulder_y")
    if shoulderX in ("", None) or shoulderY in ("", None):
        return {"ball_angle": "", "wrist_angle": ""}

    angles = {}
    for column, prefix in (("ball_angle", "ball"), ("wrist_angle", "wrist")):
        farX, farY = coords.get(f"{prefix}_x"), coords.get(f"{prefix}_y")
        angles[column] = ("" if farX in ("", None) or farY in ("", None)
                          else pixelAngle(shoulderX, shoulderY, farX, farY))
    return angles


# An arm angle is measured shoulder -> ball in the x-z plane, so it cannot leave
# [-90, 90]: +90 is straight overhead, -90 straight down, and anything beyond means the
# ball came out below the shoulder on the wrong side -- geometrically impossible for a
# pitch. Statcast's own range across the 180 labels is -64.8 to +74.3.
#
# This is a guard rail, not a correction. The Ridge is linear and unbounded, so bad
# keypoints can push it anywhere; clamping stops a nonsense number reaching a caller,
# but a clamped prediction is still a wrong prediction. Callers that can report it
# should -- see angleInputs.predict, which counts and warns.
ANGLE_MIN = -90.0
ANGLE_MAX = 90.0


def clampAngle(value):
    """Hold a predicted arm angle inside the physically possible range."""
    return min(max(float(value), ANGLE_MIN), ANGLE_MAX)


def featureVector(coords):
    """The 12 features in FEATURE_COLS order, as floats, from pixel coordinates."""
    row = dict(coords)
    row.update(deriveAngles(row))
    return [float(row[column]) for column in FEATURE_COLS]
