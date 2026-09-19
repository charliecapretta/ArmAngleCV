import sys, numpy as np, pandas as pd
sys.path.insert(0, '/Users/charliecapretta/Desktop/ArmAngleAttempt')
from features import FEATURE_COLS, TARGET_COL, GROUP_COL
from AnglePredictor import load_labeled_data, make_model
from sklearn.model_selection import LeaveOneGroupOut
rng = np.random.default_rng(0)

d = load_labeled_data('/Users/charliecapretta/Desktop/ArmAngleAttempt/labels.csv').reset_index(drop=True)
X, y, g = d[FEATURE_COLS].values, d[TARGET_COL].values, d[GROUP_COL].values
pred = np.zeros(len(y))
for tr, te in LeaveOneGroupOut().split(X, y, g):
    pred[te] = make_model().fit(X[tr], y[tr]).predict(X[te])
d['pred'] = pred
print(f"LOPO Ridge RMSE {np.sqrt(np.mean((y-pred)**2)):.3f}")

# season Statcast mean per pitcher
s = pd.read_csv('/Users/charliecapretta/Desktop/ArmAngleAttempt/savant_data (10).csv', low_memory=False)
season = s.groupby('pitcher').arm_angle.agg(['mean','std','count'])
d['season'] = d[GROUP_COL].map(season['mean'])
print("labeled pitchers with season mean:", d.groupby(GROUP_COL).season.first().notna().sum(), "/", d[GROUP_COL].nunique())
d['season'] = d['season'].fillna(d.groupby(GROUP_COL)[TARGET_COL].transform('mean'))
print("season Statcast within-pitcher SD, pooled over pitchers with >=10 pitches: "
      f"{np.sqrt((season[season['count']>=10]['std']**2).mean()):.2f} deg")

# ---- variance decomposition of model error vs season mean, all pitchers ----
for col, name in [('pred','Ridge LOPO'), ('ball_angle','raw hand ball_angle')]:
    d['e'] = d[col] - d['season']
    grp = d.groupby(GROUP_COL)['e']
    multi = grp.filter(lambda x: len(x) >= 2)
    within = np.sqrt(d.loc[multi.index].groupby(GROUP_COL)['e'].var(ddof=1).mean())
    bias = grp.mean()
    n = grp.size()
    # between-pitcher bias sd, corrected for the sampling noise in each pitcher mean
    between = np.sqrt(max(bias.var(ddof=1) - (within**2 / n).mean(), 0))
    print(f"\n[{name}] error vs pitcher season Statcast angle")
    print(f"  overall bias {d.e.mean():+.2f}  within-pitcher SD {within:.2f}  between-pitcher bias SD {between:.2f}  (pitchers with >=2: {(n>=2).sum()})")
    print(f"  pitchers within 1 deg of season mean after ONE pitch: {(d.e.abs()<1).mean()*100:.0f}% of pitches")
    # simulate mean-of-n error: bias ~ N(0,between), noise ~ N(0,within)/sqrt(n)
    print("  simulated new pitcher, P(|mean of n preds - season angle| < 1 deg):")
    for k in [1,2,3,5,8,10,15,20,30,50,100]:
        b = rng.normal(0, between, 200000); z = rng.normal(0, within/np.sqrt(k), 200000)
        print(f"    n={k:>3}: {np.mean(np.abs(b+z)<1)*100:5.1f}%   noise-only SE {within/np.sqrt(k):.2f} deg")
    d.drop(columns='e', inplace=True)

# ---- empirical running mean on pitchers with >=5 pitches ----
print("\n=== empirical running mean, pitchers with >=5 labels (1000 random orderings) ===")
big = [p for p, c in d.groupby(GROUP_COL).size().items() if c >= 5]
for col in ['pred', 'ball_angle']:
    print(f"\n[{col}]  median |running mean - season angle| by n  (first-n that stays <1 deg thereafter)")
    for p in big:
        sub = d[d[GROUP_COL]==p]; v = sub[col].values; tgt = sub.season.iloc[0]; m = len(v)
        curves = []
        for _ in range(1000):
            o = rng.permutation(v); curves.append(np.abs(np.cumsum(o)/np.arange(1,m+1) - tgt))
        curves = np.array(curves); med = np.median(curves, 0)
        # per ordering: smallest n such that all later running means are within 1 deg
        stab = []
        for c in curves:
            ok = np.where(np.flip(np.cumsum(np.flip(c)>=1))==0)[0]
            stab.append(ok[0]+1 if len(ok) else np.nan)
        stab = np.array(stab)
        pct = f"{np.nanmedian(stab):.0f}" if np.isfinite(stab).mean()>0.5 else "never"
        print(f"  {sub.pitcher_name.iloc[0]:<18} n={m:>2} season {tgt:+6.1f} finalmean {v.mean():+6.1f} bias {v.mean()-tgt:+5.1f} sd {v.std(ddof=1):4.1f} | "
              + " ".join(f"{x:4.1f}" for x in med[:10]) + f" | stabilizes n={pct} ({np.isfinite(stab).mean()*100:.0f}% of orderings)")

# ---- "within 1 percent" stabilization: successive running means differ by <1% of |final| ----
print("\n=== 1% criterion: n at which adding a pitch moves the running mean < 1% of the pitcher's angle (median over orderings) ===")
for p in big:
    sub = d[d[GROUP_COL]==p]; v = sub.pred.values; m = len(v); tgt = sub.season.iloc[0]
    tol = 0.01*abs(tgt); res=[]
    for _ in range(1000):
        o = rng.permutation(v); rm = np.cumsum(o)/np.arange(1,m+1); step = np.abs(np.diff(rm))
        ok = np.where(np.flip(np.cumsum(np.flip(step)>=tol))==0)[0]
        res.append(ok[0]+2 if len(ok) else np.nan)
    res = np.array(res)
    print(f"  {sub.pitcher_name.iloc[0]:<18} angle {tgt:+6.1f} tol {tol:.2f} deg  n={np.nanmedian(res):.0f}  ({np.isfinite(res).mean()*100:.0f}% of orderings reach it within {m})")
