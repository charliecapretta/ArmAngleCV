"""CSV contract for saved manual analyses; never exports browser predictions."""
import csv
import io
import math
import os
import uuid

from armangle import angleInputs
from armangle import features
MODEL_VERSION = 'manual-v1'
ESTIMATE_SCOPE = ('Estimated arm angle from an MLB-trained model. '
                  'Accuracy on college footage has not been established.')
FIELDS = ['session_id', 'session_name', 'pitch_id', 'pitcher_name', 'video_name',
          'release_frame', 'release_time_sec', 'measured_angle_deg',
          'predicted_arm_angle_deg', 'status', 'release_quality', 'point_quality',
          'model_version', 'estimate_scope']


def sessionId(pitch):
    # Old local registrations sharing a reel become one stable session without rewriting them.
    return pitch.get('sessionId') or uuid.uuid5(
        uuid.NAMESPACE_URL, pitch.get('sourcePath') or f"{pitch.get('gamePk')}_{pitch.get('playId')}"
    ).hex


def enrichPitch(pitch):
    return dict(pitch, sessionId=sessionId(pitch),
                sessionName=pitch.get('sessionName') or pitch.get('park') or pitch.get('sourceName') or 'MLB example')


def safeText(value):
    text = str(value or '')
    # CSV quoting alone does not stop spreadsheet formula evaluation.
    if text.lstrip().startswith(('=', '+', '-', '@')) or text.startswith(('\t', '\r', '\n')):
        return "'" + text
    return text


def exportCsv(rows, pitches, bundle, selectedSession):
    selected = {f"{p['gamePk']}_{p['playId']}": enrichPitch(p) for p in pitches
                if sessionId(p) == selectedSession}
    if not selected:
        raise ValueError('Select an available video session to export.')
    output = []
    for row in rows:
        key = f"{row['game_pk']}_{row['play_id']}"
        if key not in selected:
            continue
        pitch = selected[key]
        status, measured, predicted = row.get('status', 'incomplete'), '', ''
        if status == 'labeled':
            try:
                angleInputs.vector(row, bundle.get('feature_mode', 'raw'))
                for axis, dimension in [('x', 'img_w'), ('y', 'img_h')]:
                    if not all(0 <= float(row[f'{name}_{axis}']) < float(row[dimension]) for name in features.KEYPOINT_NAMES):
                        raise ValueError('Points outside frame')
            except (ValueError, KeyError, TypeError):
                status = 'incomplete'
            else:
                measured = features.deriveAngles(row)['ball_angle']
                predicted = float(angleInputs.predict(bundle, [row])[0])
        frame, seconds = '', ''
        try:
            value = float(row.get('frame_index', ''))
            if math.isfinite(value) and value >= 0 and value.is_integer():
                frame = int(value)
        except (ValueError, TypeError):
            pass
        try:
            value = float(row.get('release_time_sec', ''))
            if math.isfinite(value) and value >= 0:
                seconds = value
        except (ValueError, TypeError):
            pass
        output.append(dict(session_id=selectedSession, session_name=safeText(pitch['sessionName']),
                           pitch_id=key, pitcher_name=safeText(pitch.get('pitcherName') or row.get('pitcher_name')),
                           video_name=safeText(os.path.basename((pitch.get('sourceName') or 'MLB clip').replace('\\', '/'))),
                           release_frame=frame, release_time_sec=seconds, measured_angle_deg=measured,
                           predicted_arm_angle_deg=predicted, status=status,
                           release_quality=safeText(row.get('release_quality') or 'unreviewed'),
                           point_quality=safeText(row.get('point_quality') or 'unreviewed'),
                           model_version=MODEL_VERSION, estimate_scope=ESTIMATE_SCOPE))
    if not output:
        raise ValueError('Save at least a release frame before exporting this session.')
    output.sort(key=lambda r: (r['release_frame'] if r['release_frame'] != '' else float('inf'), r['pitch_id']))
    buffer = io.StringIO(newline='')
    writer = csv.DictWriter(buffer, fieldnames=FIELDS)
    writer.writeheader()
    writer.writerows(output)
    return buffer.getvalue()
