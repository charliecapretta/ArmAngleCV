"""
Phase 3 -- does the 2D pixel angle predict Statcast's arm_angle, and does the
ball beat the wrist as the far endpoint?

Reports, on the same pitches and the same hand-picked release frames:

  shoulder -> wrist   the proxy the first 100 pitches were labeled with
  shoulder -> ball    Statcast's own basis (it derives arm_angle from
                      relative_shoulder_x/shoulder_z to
                      relative_release_ball_x/release_ball_z)

Because both are measured on identical pitches at identical frames, the
comparison is paired -- a within-pitch test, not two separate samples.

Usage:
    python analyzeLabels.py
    python analyzeLabels.py --outlierLimit 15
"""

import argparse
import collections
import csv
import json
import math
import os
import sys

import numpy as np


def pixelAngle(shoulder, far):
    """
    Angle at the shoulder, up from horizontal, in image coordinates (y grows
    downward, hence the negated dy). Overhead +90, sidearm 0, submarine negative.
    """
    if shoulder is None or far is None:
        return None
    return math.degrees(math.atan2(-(far[1] - shoulder[1]), abs(far[0] - shoulder[0])))


def point(row, prefix):
    x, y = row.get(f"{prefix}_x", ""), row.get(f"{prefix}_y", "")
    if x in ("", None) or y in ("", None):
        return None
    return (float(x), float(y))


def describe(name, err):
    err = np.asarray(err)
    if not len(err):
        return f"{name:<22} (no data)"
    return (f"{name:<22} n={len(err):<4} MAE {np.abs(err).mean():5.2f}  "
            f"RMSE {math.sqrt((err ** 2).mean()):5.2f}  bias {err.mean():+5.2f}  "
            f"max {np.abs(err).max():5.1f}")


def regressionReport(px, tv, label):
    from scipy import stats
    px, tv = np.asarray(px), np.asarray(tv)
    if len(px) < 3:
        print(f"  {label}: too few points")
        return
    fit = stats.linregress(px, tv)
    tcrit = stats.t.ppf(0.975, len(px) - 2)
    sLo, sHi = fit.slope - tcrit * fit.stderr, fit.slope + tcrit * fit.stderr
    iLo, iHi = (fit.intercept - tcrit * fit.intercept_stderr,
                fit.intercept + tcrit * fit.intercept_stderr)
    identity = tv - px
    fitted = tv - (fit.slope * px + fit.intercept)
    print(f"  {label}")
    print(f"    r = {fit.rvalue:.4f}")
    print(f"    slope     {fit.slope:.4f}  95% CI [{sLo:.4f}, {sHi:.4f}]"
          f"  {'includes 1 -> identity not rejected' if sLo <= 1 <= sHi else 'EXCLUDES 1'}")
    print(f"    intercept {fit.intercept:+.3f}  95% CI [{iLo:+.3f}, {iHi:+.3f}]"
          f"  {'includes 0' if iLo <= 0 <= iHi else 'EXCLUDES 0'}")
    print(f"    identity MAE {np.abs(identity).mean():.3f} / RMSE {math.sqrt((identity**2).mean()):.3f}")
    print(f"    fitted   MAE {np.abs(fitted).mean():.3f} / RMSE {math.sqrt((fitted**2).mean()):.3f}")
    verdict = ("calibration adds nothing measurable"
               if np.abs(fitted).mean() >= np.abs(identity).mean()
               else "calibration helps slightly")
    print(f"    -> {verdict}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--labels", default="labels.csv")
    parser.add_argument("--outlierLimit", type=float, default=15.0,
                        help="errors beyond this are reported separately, not pooled")
    args = parser.parse_args()

    rows = [r for r in csv.DictReader(open(args.labels))
            if r.get("status") == "labeled" and r.get("shoulder_x")]
    if not rows:
        print("No labeled rows.")
        return 1

    recs = []
    for row in rows:
        shoulder = point(row, "shoulder")
        pitcherId, pitcherName = None, ""
        metaPath = os.path.join("pitches", f"{row['game_pk']}_{row['play_id']}", "meta.json")
        if os.path.exists(metaPath):
            with open(metaPath) as handle:
                meta = json.load(handle)
            pitcherId, pitcherName = meta.get("pitcherId"), meta.get("pitcherName", "")
        rec = {
            "playId": row["play_id"][:8], "park": row.get("park", ""),
            "throws": row.get("p_throws", ""), "frame": int(row["frame_index"]),
            "truth": float(row["true_arm_angle"]),
            "wristAngle": pixelAngle(shoulder, point(row, "wrist")),
            "ballAngle": pixelAngle(shoulder, point(row, "ball")),
            "shoulder": shoulder, "offShoulder": point(row, "off_shoulder"),
            "wrist": point(row, "wrist"), "ball": point(row, "ball"),
            "pitcherId": pitcherId, "pitcherName": pitcherName,
        }
        recs.append(rec)

    print(f"labels: {len(recs)} labeled")
    withBall = [r for r in recs if r["ballAngle"] is not None]
    print(f"  with ball point: {len(withBall)}   "
          f"still to top up: {len(recs) - len(withBall)}")
    print()

    # ---- outliers first: they distort every pooled statistic ----
    print("=== OUTLIERS ===")
    flagged = []
    for rec in recs:
        basis = rec["ballAngle"] if rec["ballAngle"] is not None else rec["wristAngle"]
        if basis is None:
            continue
        err = rec["truth"] - basis
        if abs(err) > args.outlierLimit:
            mirrored = abs(basis + rec["truth"]) < 8
            flagged.append((rec, err, mirrored))
    if flagged:
        for rec, err, mirrored in sorted(flagged, key=lambda t: -abs(t[1])):
            note = "  <- sign-mirrored: shoulder/wrist SWAPPED" if mirrored else ""
            print(f"  {rec['playId']} {rec['park']:<4} f{rec['frame']:<4} "
                  f"true {rec['truth']:+6.1f}  err {err:+6.1f}{note}")
        print(f"  {len(flagged)} excluded from the pooled figures below")
    else:
        print("  none")
    print()

    flaggedIds = {rec["playId"] for rec, _, _ in flagged}
    clean = [r for r in recs if r["playId"] not in flaggedIds]

    # ---- paired wrist vs ball ----
    print("=== WRIST vs BALL (paired, same pitches and frames) ===")
    paired = [r for r in clean if r["ballAngle"] is not None and r["wristAngle"] is not None]
    if not paired:
        wristOnly = [r["truth"] - r["wristAngle"] for r in clean if r["wristAngle"] is not None]
        print("  no ball points yet -- wrist baseline only")
        print("  " + describe("shoulder->wrist", wristOnly))
    else:
        wErr = np.array([r["truth"] - r["wristAngle"] for r in paired])
        bErr = np.array([r["truth"] - r["ballAngle"] for r in paired])
        print("  " + describe("shoulder->wrist", wErr))
        print("  " + describe("shoulder->ball", bErr))
        delta = np.abs(wErr).mean() - np.abs(bErr).mean()
        from scipy import stats
        tstat, pval = stats.ttest_rel(np.abs(wErr), np.abs(bErr))
        print(f"\n  ball improves MAE by {delta:+.3f} deg  (paired t p={pval:.4f})")
        print("  -> " + ("ball is meaningfully better" if delta > 0 and pval < 0.05
                         else "no significant difference; the wrist proxy was fine"))
    print()

    # ---- regression / is calibration needed ----
    print("=== IS CALIBRATION NEEDED? ===")
    for label, key in (("shoulder->wrist", "wristAngle"), ("shoulder->ball", "ballAngle")):
        pts = [(r[key], r["truth"]) for r in clean if r[key] is not None]
        if len(pts) >= 3:
            regressionReport([p[0] for p in pts], [p[1] for p in pts], label)
    print()

    # ---- torso rotation: does the shoulder line explain any residual? ----
    print("=== TORSO ROTATION ===")
    torso = [r for r in clean
             if r["offShoulder"] and r["shoulder"] and r["wrist"] and r["wristAngle"] is not None]
    if len(torso) < 10:
        print(f"  only {len(torso)} pitches have both shoulders -- need more top-ups")
    else:
        from scipy import stats
        basis = lambda r: r["ballAngle"] if r["ballAngle"] is not None else r["wristAngle"]
        err = np.array([r["truth"] - basis(r) for r in torso])
        # Shoulder-line length relative to arm length is a foreshortening proxy:
        # square to the camera the line is long, side-on it shortens.
        ratio = np.array([
            math.hypot(r["offShoulder"][0] - r["shoulder"][0],
                       r["offShoulder"][1] - r["shoulder"][1]) /
            max(math.hypot(r["wrist"][0] - r["shoulder"][0],
                           r["wrist"][1] - r["shoulder"][1]), 1e-6)
            for r in torso])
        tilt = np.array([
            math.degrees(math.atan2(-(r["offShoulder"][1] - r["shoulder"][1]),
                                    r["offShoulder"][0] - r["shoulder"][0]))
            for r in torso])
        for name, arr in (("shoulder-line / arm length", ratio), ("shoulder-line tilt", tilt)):
            r_, p_ = stats.pearsonr(arr, err)
            print(f"  residual vs {name:<28} r={r_:+.3f}  p={p_:.4f}"
                  f"  {'<- explains some residual' if p_ < 0.05 else ''}")
    print()

    # ---- breakdowns ----
    basisOf = lambda r: r["ballAngle"] if r["ballAngle"] is not None else r["wristAngle"]
    usable = [r for r in clean if basisOf(r) is not None]
    err = np.array([r["truth"] - basisOf(r) for r in usable])

    # ---- does any fitted model beat using the angle raw? (held-out) ----
    full = [r for r in clean if r["ballAngle"] is not None and r["offShoulder"] and r["shoulder"]]
    if len(full) >= 40:
        from sklearn.model_selection import GroupKFold, KFold
        ball = np.array([r["ballAngle"] for r in full])
        tilt = np.array([math.degrees(math.atan2(-(r["offShoulder"][1] - r["shoulder"][1]),
                                                 r["offShoulder"][0] - r["shoulder"][0]))
                         for r in full])
        hand = np.array([1.0 if r["throws"] == "R" else 0.0 for r in full])
        truth = np.array([r["truth"] for r in full])
        groups = np.array([r["pitcherId"] if r["pitcherId"] is not None else i
                           for i, r in enumerate(full)])
        # Folds MUST be split by pitcher. A pitcher's arm angle is nearly constant,
        # so random folds let a model memorise pitchers seen in training -- which
        # made torso tilt look like a 0.1 deg win when a pitcher-grouped split
        # shows it is not.
        kf = GroupKFold(n_splits=8)

        def crossVal(columns):
            pred = np.zeros_like(truth)
            for train, test in kf.split(ball, truth, groups=groups):
                if columns is None:
                    pred[test] = ball[test]
                    continue
                if columns == ():
                    # Offset the angle by the training bias -- not a constant fit.
                    pred[test] = ball[test] + (truth[train] - ball[train]).mean()
                    continue
                design = lambda i: np.c_[tuple(c[i] for c in columns) + (np.ones(len(i)),)]
                coef, *_ = np.linalg.lstsq(design(train), truth[train], rcond=None)
                pred[test] = design(test) @ coef
            e = truth - pred
            return np.abs(e).mean(), math.sqrt((e ** 2).mean())

        print("=== DOES FITTING BEAT THE RAW ANGLE? ===")
        print("  (8-fold, folds split by PITCHER -- no pitcher in both train and test)")
        for name, cols in (("ball angle, no fit", None),
                           ("+ intercept", ()),
                           ("+ slope & intercept", (ball,)),
                           ("+ torso tilt", (ball, tilt)),
                           ("+ tilt & handedness", (ball, tilt, hand))):
            mae, rmse = crossVal(cols)
            print(f"  {name:<24} MAE {mae:5.2f}  RMSE {rmse:5.2f}")
        print()

        # Per-pitcher offsets: the dominant residual term, and the reason no
        # global fit generalises to an unseen pitcher.
        print("=== PER-PITCHER BIAS (>=5 pitches) ===")
        byPitcher = collections.defaultdict(list)
        for rec in full:
            byPitcher[rec["pitcherName"]].append(rec)
        table = []
        for name, group in byPitcher.items():
            if len(group) < 5:
                continue
            e = np.array([g["truth"] - g["ballAngle"] for g in group])
            table.append((np.mean([g["truth"] for g in group]), name, len(group),
                          e.mean(), e.std(), len({g["park"] for g in group})))
        if table:
            print(f"  {'pitcher':<24}{'n':>4}{'trueAvg':>9}{'bias':>8}{'sd':>7}{'parks':>7}")
            for trueAvg, name, n, bias, sd, parks in sorted(table):
                flag = "  <<" if abs(bias) > 3 else ""
                print(f"  {name:<24}{n:>4}{trueAvg:>+9.1f}{bias:>+8.2f}{sd:>7.2f}{parks:>7}{flag}")
            spread = max(t[3] for t in table) - min(t[3] for t in table)
            withinSd = np.mean([t[4] for t in table])
            print(f"\n  between-pitcher bias spread {spread:.1f} deg"
                  f"  vs mean within-pitcher sd {withinSd:.2f} deg")
            print("  -> " + ("residual is dominated by a per-pitcher offset, not by "
                             "click noise" if spread > 2 * withinSd else
                             "per-pitcher offsets are small relative to noise"))
        print()

    print("=== BY HANDEDNESS ===")
    for hand in ("L", "R"):
        mask = np.array([r["throws"] == hand for r in usable])
        if mask.sum():
            print(f"  {hand}: n={mask.sum():<3} bias {err[mask].mean():+5.2f}  "
                  f"MAE {np.abs(err[mask]).mean():.2f}")
    print()

    print("=== COVERAGE BY TRUE ANGLE ===")
    for lo, hi in [(-70, -30), (-30, -10), (-10, 10), (10, 30), (30, 50), (50, 80)]:
        mask = np.array([lo <= r["truth"] < hi for r in usable])
        cell = f"MAE {np.abs(err[mask]).mean():.2f}" if mask.sum() else "-"
        flag = "  << THIN" if 0 < mask.sum() < 8 else ("  << EMPTY" if not mask.sum() else "")
        print(f"  {lo:>4} to {hi:>3}:  n={mask.sum():<3} {cell}{flag}")
    print()

    print("=== BY PARK ===")
    byPark = collections.defaultdict(list)
    for rec, e in zip(usable, err):
        byPark[rec["park"]].append(e)
    biases = [np.mean(v) for v in byPark.values() if len(v) >= 3]
    for park in sorted(byPark, key=lambda k: -len(byPark[k])):
        v = np.array(byPark[park])
        print(f"  {park:<4} n={len(v):<3} bias {v.mean():+6.2f}  MAE {np.abs(v).mean():5.2f}")
    if biases:
        # Compare the observed spread of park means against the spread pure
        # sampling noise would produce at these group sizes. With n=3-9 per park
        # the noise floor is large, so a raw SD comparison against MAE badly
        # overstates park structure -- checked directly: adding per-park
        # intercepts made 10-fold held-out MAE worse (2.64 vs 2.54).
        sigma = err.std(ddof=1)
        sizes = [len(v) for v in byPark.values() if len(v) >= 3]
        expected = math.sqrt(np.mean([sigma ** 2 / n for n in sizes]))
        observed = np.std(biases)
        print(f"\n  park bias SD {observed:.2f} deg")
        print(f"  expected from sampling noise alone at these group sizes: {expected:.2f} deg")
        print("  -> " + ("indistinguishable from noise; POOL, don't fit per park"
                         if observed <= expected * 1.5
                         else "exceeds the noise floor; per-park effects may be real"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
