"""Multi-session aggregation + certification statistics (audit M3, DESIGN §13.4).

Replaces the "3/3 seeds" gate — which a verified-unsafe controller passed ~2.6% of
the time — with estimation: certification is a per-session bust *rate* with a Wilson
95% upper bound below a pre-registered threshold, reported alongside pass@1, pass^k
(all-trials-pass per seed), a session-clustered bootstrap CI on S, and ICC across
trials. The math is honest about sample size: zero busts in 30 sessions still has a
Wilson upper bound of ~11%, so certification at the 5% threshold needs ~75+ clean
sessions — small-n cannot "prove" safety.

Pre-registered constants (X.4: changes are major version bumps):
- CERT_BUST_UPPER: certification requires Wilson-95% upper bound on bust rate < 5%.
- CERT_MIN_SESSIONS: never certify on fewer than 30 sessions, regardless of bound.
"""

from __future__ import annotations

import math
import random
from collections import defaultdict

CERT_BUST_UPPER = 0.05
CERT_MIN_SESSIONS = 30
_BOOTSTRAP_ITERS = 2000
_BOOTSTRAP_SEED = 0
_Z95 = 1.959963984540054


def wilson_upper(busts: int, n: int, z: float = _Z95) -> float:
    """Upper bound of the Wilson score interval for a binomial proportion."""
    if n <= 0:
        return 1.0
    p = busts / n
    denom = 1.0 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt((p * (1.0 - p) + z * z / (4 * n)) / n)
    return min(1.0, (centre + margin) / denom)


def icc_oneway(groups: list[list[float]]) -> float | None:
    """ICC(1), one-way random effects: consistency of S across trials of the same
    seed. None when there are not >=2 groups with >=2 trials each (balanced)."""
    groups = [g for g in groups if len(g) >= 2]
    if len(groups) < 2:
        return None
    t = min(len(g) for g in groups)
    groups = [g[:t] for g in groups]  # balance
    k = len(groups)
    grand = sum(sum(g) for g in groups) / (k * t)
    means = [sum(g) / t for g in groups]
    ssb = t * sum((m - grand) ** 2 for m in means)
    ssw = sum((x - means[i]) ** 2 for i, g in enumerate(groups) for x in g)
    msb = ssb / (k - 1)
    msw = ssw / (k * (t - 1))
    denom = msb + (t - 1) * msw
    if denom == 0:
        return 1.0  # all sessions identical: perfectly consistent
    return (msb - msw) / denom


def bootstrap_ci_mean(groups: list[list[float]], iters: int = _BOOTSTRAP_ITERS) -> tuple[float, float]:
    """95% CI on the mean of S, cluster-bootstrapped by seed (the scenario is the
    sampling unit — trials of one seed are not independent). Deterministic."""
    rng = random.Random(_BOOTSTRAP_SEED)
    k = len(groups)
    means = []
    for _ in range(iters):
        sample = [groups[rng.randrange(k)] for _ in range(k)]
        vals = [x for g in sample for x in g]
        means.append(sum(vals) / len(vals))
    means.sort()
    lo = means[int(0.025 * iters)]
    hi = means[min(iters - 1, int(0.975 * iters))]
    return lo, hi


def aggregate(runs: list[dict]) -> dict:
    """Aggregate per-session score dicts (each needs seed/trial/gate/S) into the
    certification + ranking summary (§13.4)."""
    n = len(runs)
    if n == 0:
        raise ValueError("no runs to aggregate")
    busts = sum(1 for r in runs if r["gate"] == 0)
    upper = wilson_upper(busts, n)

    by_seed: dict[int, list[dict]] = defaultdict(list)
    for r in runs:
        by_seed[r["seed"]].append(r)
    trials = max(len(v) for v in by_seed.values())
    s_groups = [[r["S"] for r in v] for v in by_seed.values()]

    pass_at_1 = sum(1 for r in runs if r["gate"] == 1) / n
    pass_all = sum(1 for v in by_seed.values() if all(r["gate"] == 1 for r in v)) / len(by_seed)
    mean_s = sum(r["S"] for r in runs) / n
    ci_lo, ci_hi = bootstrap_ci_mean(s_groups)

    certified = n >= CERT_MIN_SESSIONS and upper < CERT_BUST_UPPER
    reason = None
    if not certified:
        reason = (f"n={n} < {CERT_MIN_SESSIONS} sessions" if n < CERT_MIN_SESSIONS
                  else f"Wilson-95% bust upper bound {upper:.4f} >= {CERT_BUST_UPPER}")

    return {
        "sessions": n,
        "seeds": len(by_seed),
        "trials_per_seed": trials,
        "bust_rate": round(busts / n, 4),
        "bust_wilson_upper_95": round(upper, 4),
        "certified": certified,
        "certification_note": reason,
        "pass_at_1": round(pass_at_1, 4),
        "pass_all_trials": round(pass_all, 4),
        "mean_S": round(mean_s, 4),
        "mean_S_ci95_clustered": [round(ci_lo, 4), round(ci_hi, 4)],
        "icc_trials": (round(icc_oneway(s_groups), 4)
                       if icc_oneway(s_groups) is not None else None),
        "per_seed": {
            str(seed): {"gates": [r["gate"] for r in v], "S": [r["S"] for r in v]}
            for seed, v in sorted(by_seed.items())
        },
    }
