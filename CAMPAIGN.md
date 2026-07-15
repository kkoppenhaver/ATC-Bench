# Full Certification Campaign — Plan & Budget (pre-registration draft)

Status: **draft awaiting spend approval** · Drafted 2026-07-14 · Governs P3.6.4's
"full campaign" step. Runs land under `runs/campaign/`. This document is committed
*before* any campaign session runs; changes after launch are amendments, logged below.

## 1. Goal

Produce the first quotable ATC-Bench numbers: per-cell bust rates with honest
Wilson-95% bounds, mean S with seed-clustered CIs, ICC across trials, and the
Haiku-vs-Sonnet separation — under a fixed, pre-registered scoring and harness
configuration. Certification badges are a *conditional second stage*, not the
default output (see §4).

## 2. Pre-registered configuration (pinned before stage 1)

| What | Value | Where pinned |
|---|---|---|
| Harness version | 0.1.0 (`atcbench/__init__.py`) | run records |
| Prompt templates | cd-v5 / gnd-v3 / twr-v3 | `system_prompt.py` |
| Scoring weights | CD/GND/TWR `W_E, W_H, W_F, W_A` as in `scoring/*.py` | code |
| Cardinal thresholds | NEGLECT 180 s (CD/GND/TWR), STRANDED 300 s, DEADLOCK 300 s | code |
| Pilot re-calls | `PILOT_RECALL_SEC = 90`, all positions (P4.0f) | code |
| Context window | trim trigger 60k / target 20k tokens, all models (P4.0g) | adapter defaults, echoed in `score.model` |
| Certification rule | Wilson-95% bust upper < 5% AND ≥ 30 sessions | `aggregate.py` |
| Session length | 3600 sim-seconds | CLI default |
| Models | `claude-haiku-4-5` ($1/$5 per MTok), Sonnet 5 (`claude-sonnet-5`, $3/$15) | this doc |
| Budget-truncation rule | a session with `budget_exhausted: true` **and** unserviced traffic remaining at exhaustion is **invalid**, not a bust: re-run once at 2× cap; if it exhausts again, stop and re-cost the cell. Exhaustion with no traffic remaining (post-service idle) is valid. | this doc |
| Seeds | 1–20 (scenario generator may reroll infeasible candidates; the recorded seed is canonical) | this doc |

Anything in this table changing after stage 1 starts = amendment logged in §8 and a
major-version note per X.4.

## 3. Stage 1 — calibration grid

Cells chosen from the pilot (26 sessions) and the post-P4.0f/g probes. CD-standard is
excluded (saturated in the pilot — uninformative). TWR-calm exists to give Haiku a
gradient; Sonnet skips it (its frontier is at standard).

| Cell | Model | Seeds × trials | Sessions | $/session (est) | Cap | Cell cost (est) |
|---|---|---|---|---|---|---|
| CD heavy · turn | Haiku | 20 × 3 | 60 | 0.22 | 1 | $13 |
| CD heavy · metered | Haiku | 20 × 3 | 60 | 0.20 | 1 | $12 |
| GND standard · turn | Haiku | 20 × 2 | 40 | **3.50 (confirmed)** | 6 | $140 |
| TWR calm · turn | Haiku | 20 × 2 | 40 | 1.25 | 3 | $50 |
| TWR standard · turn | Haiku | 20 × 2 | 40 | 3.50 | 6 | $140 |
| CD heavy · turn | Sonnet | 20 × 3 | 60 | 0.55 | 2 | $33 |
| CD heavy · metered | Sonnet | 20 × 3 | 60 | 0.55 | 2 | $33 |
| GND standard · turn | Sonnet | 20 × 2 | 40 | ~10 (pre-flight, §6) | 15 | $400 |
| TWR standard · turn | Sonnet | 20 × 2 | 40 | ~10 (pre-flight, §6) | 15 | $400 |

**Stage 1 estimate: ~$1,220** · with 15% contingency: **~$1,400**.

Trials rationale: 3 trials on CD (cheap; ICC needs trials) and 2 on GND/TWR (ICC(1)
needs ≥2; the third trial on a $3.50–10 session buys little precision for its price —
the seed, not the trial, is the sampling unit).

## 4. Stage 2 — conditional certification push

Certification math: 0 busts/75 sessions → Wilson upper 4.87% (certifies); 1 bust
needs n≈155. Therefore stage 2 triggers **only** on cells with **zero stage-1
busts**, topping the cell up to 75–90 sessions. Pre-registered trigger, decided
mechanically from stage-1 `summary.json`. Realistic candidates on pilot evidence:
Sonnet CD-turn, possibly none. **Reserve: up to $300** (spent only if triggered).

Cells that don't trigger publish their bust rate + bound with
`certified: false` and the reason — that *is* the result, not a failure of the
campaign.

## 5. What gets published

Per cell: `summary.json` (bust rate, Wilson upper, pass@1, pass^k, mean S,
clustered CI, ICC, per-seed detail) + full run dirs (committed, per repo
convention). Headline claims are limited to: per-cell bust rates with bounds,
mean-S comparisons with CIs, the metering delta, and the Haiku/Sonnet separation.
No "safe"/"certified" language for any cell that doesn't pass §4.

## 6. Pre-flight checklist (before stage 1 spend)

1. **Sonnet cost probe** — 2 × GND-standard sessions at pinned trims (~$20–25),
   because the $10/session figure is extrapolated from price ratios, not measured.
   If actual > $12/session, re-cost the Sonnet tier before proceeding.
2. **TWR full-length sanity** — 1 × TWR-standard Haiku session at $6 cap (~$3.50):
   no full-length TWR session has run since P4.0f/g landed (wake-timing state is the
   most trim-sensitive picture; confirm no crash/exhaustion and eyeball the transcript).
3. **Invalid-session tooling** — `evaluate` currently aggregates every session;
   add the §2 budget-truncation rule (flag + exclude invalid sessions from
   aggregation, report them separately). Small code change + test, lands before
   stage 1.
4. Suite green, ruff clean, working tree committed (campaign runs on a tagged
   commit).

## 7. Execution mechanics

- One background script per cell (as the probes ran), cells in parallel, each
  writing `runs/campaign/<model>_<cell>/`; within-cell sessions are sequential
  (`evaluate`).
- Wall clock: GND/TWR sessions ≈ 20–30 min ⇒ a 40-session cell ≈ 13–20 h; with all
  cells parallel, stage 1 ≈ 1–2 days. Watch API rate limits; cells can be staggered.
- Spend tracking: every invocation carries `--max-usd` + explicit per-MTok prices;
  running total reviewed at each cell completion; hard stop if cumulative spend
  exceeds approved tier + contingency.
- Each cell commit includes its summary and any invalid-session re-runs.

## 8. Amendments

(None yet.)

## Alternative tiers (for approval)

| Tier | Change vs. §3 | Est. total (with contingency) |
|---|---|---|
| **Recommended (§3)** | — | **~$1,400** + $300 conditional reserve |
| Full P3.6.4 literal | 3 trials everywhere, Sonnet TWR-calm added | ~$2,400 + reserve |
| Lean | GND/TWR cells at 15 seeds × 2 (30 sessions, the certification floor; 0/30 bound is 11.4%) | ~$1,050 + reserve |

The old "$150–250" estimate predates the budget-truncation discovery: it was priced
off sessions that were silently truncated at 40–75% completion. Full-length
GND/TWR sessions cost what §3 says they cost.
