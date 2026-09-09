"""Release timing utilities. No image labels or trained models are modified.

Statcast velocities/accelerations refer to y=50 feet. The trajectory is a
constant-acceleration approximation, not an observed video event. Plate arrival
must specify a plane: the front edge is 17/12 ft from the back tip (y=0).
Source: https://baseballsavant.mlb.com/csv-docs
"""
import math

FRONT_PLATE_Y = 17 / 12


def finiteNumber(value, name):
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f'Missing or invalid {name}') from error
    if not math.isfinite(number):
        raise ValueError(f'Missing or invalid {name}')
    return number


def timeAtY(y, vy0, ay):
    """Time relative to y=50 on the incoming (negative-velocity) branch."""
    y, vy0, ay = [finiteNumber(v, n) for v, n in [(y,'y'),(vy0,'vy0'),(ay,'ay')]]
    if vy0 >= 0:
        raise ValueError('Expected a pitch traveling toward home plate')
    dy = y - 50
    if abs(ay) < 1e-9:
        return dy / vy0
    discriminant = vy0 * vy0 + 2 * ay * dy
    if discriminant <= 0:
        raise ValueError('Trajectory cannot reach the requested plane while incoming')
    # Rationalized small root avoids cancellation near y=50.
    return 2 * dy / (vy0 - math.sqrt(discriminant))


def flightSeconds(trajectory, plateY=FRONT_PLATE_Y):
    releaseY = trajectory.get('release_pos_y')
    if releaseY in (None, ''):
        releaseY = 60.5 - finiteNumber(trajectory.get('release_extension'),'release_extension')
    releaseY = finiteNumber(releaseY, 'release_pos_y')
    plateY = finiteNumber(plateY, 'plateY')
    if not 0 <= plateY < releaseY < 60.5:
        raise ValueError('Invalid release or arrival plane')
    duration = timeAtY(plateY, trajectory.get('vy0'), trajectory.get('ay')) - timeAtY(releaseY, trajectory.get('vy0'), trajectory.get('ay'))
    if not 0 < duration < 2:
        raise ValueError('Implausible flight duration')
    return duration


def speedOnlyFlightSeconds(speedMph, extensionFeet, averageSpeedFraction=1.0, plateY=FRONT_PLATE_Y):
    speed = finiteNumber(speedMph, 'speedMph')
    extension = finiteNumber(extensionFeet, 'extensionFeet')
    fraction = finiteNumber(averageSpeedFraction, 'averageSpeedFraction')
    plane = finiteNumber(plateY, 'plateY')
    distance = 60.5 - extension - plane
    if speed <= 0 or not 0 < fraction <= 1 or not 0 <= extension < 60.5 or distance <= 0:
        raise ValueError('Invalid speed, extension, or average speed fraction')
    return distance / (speed * 22 / 15 * fraction)


def releaseFrameFromArrival(arrivalSeconds, flightDuration, frameTimes):
    """Use decoded timestamps; return None when release falls outside the clip."""
    target = finiteNumber(arrivalSeconds,'arrivalSeconds') - finiteNumber(flightDuration,'flightDuration')
    if flightDuration <= 0:
        raise ValueError('Flight duration must be positive')
    times = [finiteNumber(t,'frame timestamp') for t in frameTimes]
    if not times or any(b <= a for a,b in zip(times,times[1:])):
        raise ValueError('Need strictly increasing decoded frame timestamps')
    if target < times[0] or target > times[-1]:
        return None
    return min(range(len(times)), key=lambda i:abs(times[i]-target))
