# ATCBench — Task Breakdown

Work breakdown derived from [DESIGN.md](./DESIGN.md) §15 (build plan). Tasks are grouped
by phase and ordered by dependency. **v1 public release = Phases 1–2.**

Legend: `[ ]` todo · `[~]` in progress · `[x]` done. Each task lists the design
section(s) it implements and its hard dependencies.

> **Status (2026-07): Phases 1 and 2 are functional** (v1 = CD + GND). Deterministic
> Clearance Delivery and Ground sessions run, score, certify, and replay byte-identically
> (`python -m atcbench.cli run --position CD|GND ...`). The oracle certifies across
> seeds/bands; scripted bad controllers bust (CD: uncaught readback; GND: runway
> incursion + head-on deadlock). `[~]` = partial: P0.2 (SessionStart hook/type checker
> pending); P1.10/P2.2 (a **fictional stand-in** facility, Marlow Regional/KMRL — real
> FAA chart packs are future work); P2.5 (one ground error class so far). 81 tests, ruff clean. Token-metered regime (§4.2) on CD+GND+TWR; **Phase 3 Tower** position live (CD→GND→TWR).
>
> **2026-07 benchmark audit:** an expert audit with empirical probes found the scorers
> exploitable by no-skill baselines (a do-nothing controller certifies at GND with S=1.0;
> a blind re-clearance controller matches the oracle at CD without reading readbacks) and
> the live-model path unbuilt (no `--model`, GND/TWR prompts, or `evaluate`; `bay_read`
> is a no-op). **Phases 3.5–3.6 below are the fix gate: no Phase 4 work and no public
> claims/numbers until both exit tests pass.** Checkboxes below marked `[~]` with an
> _Audit:_ note were corrected from `[x]` to match verified reality.

---

## Phase 0 — Project scaffolding

Foundational plumbing. No behavior yet; everything else builds on this.

- [x] **P0.1 — Python package skeleton.** Create `atcbench/` package with the
  submodule layout from DESIGN §15 (`sim/`, `scenarios/`, `pilots/`, `verbalizer/`,
  `harness/`, `strips/`, `charts/`, `scoring/`, `baselines/`). Add `pyproject.toml`
  (Python 3.12+, single package `atcbench`), `runs/`, `seeds/`, `tests/` dirs.
  _Deps: none._
- [~] **P0.2 — Dev tooling & CI.** Configure formatter/linter (ruff), type checker
  (mypy or pyright), test runner (pytest), and a GitHub Actions workflow that runs
  lint + tests. Add a SessionStart hook so web sessions can run the suite. _Deps: P0.1._
- [x] **P0.3 — Core dataclasses & JSONL I/O.** World-state dataclasses (aircraft,
  world snapshot) and append-only JSONL event-log writer/reader. Define the session
  directory contract (`scenario.json`, `events.jsonl`, `transcript.jsonl`,
  `strips_history.jsonl`, `score.json`). _Deps: P0.1. Design §3.2, §4.6._
- [x] **P0.4 — Named-PRNG seed manager.** Master seed → independent named streams
  (`traffic`, `errors`, `weather`, `callsigns`, `airspace`, `coordination`). This is
  the root of all determinism. _Deps: P0.1. Design §12.1, principle #2._

---

## Phase 1 — Core + Clearance Delivery (walking skeleton)

Goal: a full CD session runs deterministically and is scorable from the log alone.
**Exit test (P1.13):** record model outputs, replay twice → byte-identical event logs.

### Simulation core
- [x] **P1.1 — Sim clock & tick loop.** 1 Hz fixed-timestep integrator, event-driven /
  cadence-based model update scheduling (CD = event-driven). No wall clock anywhere.
  _Deps: P0.3, P0.4. Design §4.1._
- [x] **P1.2 — Event log emission.** Wire every state transition to append typed events
  (`aircraft_spawn`, `radar_snapshot_sent`, `model_turn_start/end`, `transmission`,
  `fsm_intent`, `command_applied`, `strip_op`, `session_end`, …). _Deps: P0.3, P1.1.
  Design §4.6._

### Communication channel
- [~] **P1.3 — Frequency channel model.** Half-duplex single channel; broadcast duration
  `ceil(word_count / 2.5)`; transmission queueing when channel busy; `[BLOCKED]` handling
  stub. _Audit: time cost + queueing are wired at CD only — GND/TWR `_tx` is free and
  instant, and `[BLOCKED]` is absent everywhere; shared channel component → P4.0a._
  _Deps: P1.1. Design §7.1._
- [x] **P1.4 — Transcript formatter.** World→model frequency feed (ordered, timestamped
  JSON lines per §7.3). _Deps: P1.3. Design §7.3._

### Pilot agents
- [x] **P1.5 — Tiered phraseology parser.** Deterministic grammar + normalization
  (spoken-word/digit equivalence, "point"/"decimal", group vs. single-digit altitudes).
  Four parse tiers (standard / nonstandard / ambiguous / unparseable). Ships with a
  variant corpus for tests. _Deps: P0.1. Design §7.2, key risk #1._
- [x] **P1.6 — Pilot FSM.** Per-aircraft state machine (§8.2 states, CD subset). Core
  loop: draw error → emit `fsm_intent` → verbalize → apply `will_comply_as`. Correction
  window logic. Pure function of `(state, parsed_instruction, error_schedule, tick)`.
  _Deps: P1.5, P1.2. Design §8.1, §8.2._
- [~] **P1.7 — Error schedule (CD subset).** Seeded per-aircraft error schedule for CD
  classes: RB-ALT, RB-HDG, RB-FREQ, RB-DROP, RB-PART, CS-CONF, CS-WRONG, SAY-AGAIN,
  BLOCKED. _Audit: generator emits only RB-ALT/RB-FREQ/RB-PART/RB-DROP (+CS-WRONG via
  twin callsigns); RB-HDG, CS-CONF, SAY-AGAIN, BLOCKED have no generator/FSM path →
  P4.0c._ _Deps: P0.4, P1.6. Design §8.3._
- [x] **P1.8 — Verbalizer + response cache.** Pinned-model client (temp 0) rendering
  `fsm_intent`→radio string; on-disk cache keyed on `(intent_json, persona, prompt_hash)`.
  Personas: `airline_crisp`, `ga_relaxed`, `student_pilot`, `foreign_carrier`, `unfamiliar`.
  _Deps: P1.6. Design §8.3, §8.1 verbalization layer, principle #2._

### Scenario generation (CD slice)
- [x] **P1.9 — CD scenario generator.** Filed flight-plan queue (some flawed), pilot
  call schedule, persona assignment, similar-callsign injection (edit-distance-1 pairs).
  Difficulty bands (calm/standard/heavy) via error density + call rate. _Deps: P0.4,
  P1.7. Design §5.1, §6.1, §8.4, §12._

### Chart pack (CD slice)
- [~] **P1.10 — Facility CD chart pack.** _Done as a **fictional stand-in** (Marlow
  Regional / KMRL, `FACILITY_KIND="fictional"`): structured routes/SIDs, initial-altitude
  LOA, frequencies, squawk — enough to check CRAFT clearances. Remaining: a **real** FAA
  chart pack (KMDW) so the Facility-track premise holds. Design §5.1, §11.3._
  _Deps: P0.3. Design §5.1, §11.3._

### Model harness
- [~] **P1.11 — Model adapter + tool router.** Provider-agnostic adapter (Anthropic
  Messages tool schema native; OpenAI-style adapter). Tools for CD: `transmit`, strip
  tools, `wait`. State serializer, verbatim I/O logging, token-count capture. _Audit:
  scripted/bad/replay adapters work; `AnthropicAdapter` is unwired from the CLI, stubs
  every tool result as `"ok"`, has no retry policy, and `model_io.json` logs outputs but
  not the messages sent. Live path → P3.6.2._ _Deps: P1.2, P1.4. Design §11.1, §11.4,
  §3.2._
- [x] **P1.12 — System-prompt assembly.** Versioned template assembling the 9 §11.3
  sections from the chart pack; emit `prompt_hash` into the run record. _Deps: P1.10.
  Design §11.3._

### Scoring
- [~] **P1.14 — CD scorer.** Pure function of the log. Cardinal detection (NEGLECT,
  uncorrected bad readback on departed queue item), severe cap, S_raw components with
  CD-specific E (service time). Hearback H from RB-*/CS-* catch rate. _Audit: blind
  "negative …" re-clearance scored H=1.0 without listening — fixed by P3.5.2 (H is
  catch rate minus false-alarm rate; spurious corrections dock F). Remaining: E uses
  fixed thresholds instead of §13.2 oracle normalization → P3.5.5._ _Deps: P1.2.
  Design §13.1–§13.3, §6.1._
- [x] **P1.15 — Flight strip store + tools.** Bay/strip data structures, tool impls
  (`strip_create/update/move/delete`, `bay_read`), auto-strip on handoff_offered,
  `strips_history.jsonl`. _Deps: P1.11. Design §9.2, §9.3._

### Determinism & CLI
- [x] **P1.13 — `atcbench replay` + determinism CI check.** Replay recorded model
  outputs through the harness; assert byte-identical event logs across two replays.
  **This is the Phase 1 exit test.** _Deps: P1.11, P1.14. Design §17.2._
- [x] **P1.16 — CLI skeleton (`run`, `score`, `replay`).** Argparse/click surface per
  §17.1 for the CD slice. _Deps: P1.11, P1.14, P1.13. Design §17.1._

---

## Phase 2 — Ground (completes v1 release)

**Exit test:** oracle certifies 3/3 at standard band; scripted "bad controller" busts reliably.

- [x] **P2.1 — Taxi graph model.** Directed graph (nodes: intersections, runway
  entry/exit, gates, hold-short bars; edges: segments with length/direction, `runway`
  flag). Aircraft taxi kinematics (15 kt straight / 8 kt turn). _Deps: P1.1. Design §4.4._
- [~] **P2.2 — Real facility taxi-diagram chart pack.** _Done as a **fictional stand-in**
  (Marlow Regional / KMRL): a hand-built surface graph with the crossing-runway geometry +
  human-readable description. Remaining: a real airport surface graph (KMDW) parsed from the
  FAA diagram. Deps: P2.1, P1.10. Design §4.4, §5.1._
- [~] **P2.3 — Ground clearance parsing.** Taxi-route + explicit-crossing + hold-short
  phraseology (no implied crossings). Extends the parser. _Audit: parsing exists, but
  departures are assigned the canonical route regardless of what the model transmits
  (bare "taxi" with no route works), and hold-short instructions are parsed but never
  enforced or scored — the routing skill isn't measured → P3.5.10 (route production),
  P4.0b (route robustness)._ _Deps: P1.5, P2.1. Design §6.2._
- [x] **P2.4 — Incursion & deadlock detection.** Runway incursion (RI-CTRL provenance:
  model clearance vs. injected FSM error) and head-on deadlock oracle. _Deps: P2.1.
  Design §4.4, §4.5, §13.1._
- [~] **P2.5 — Ground FSM states & error subset.** Wrong-turn compliance, dropped
  hold-short readback, hot-spot stop, progressive-taxi (`unfamiliar` persona). _Deps:
  P1.6, P2.1. Design §6.2, §8.3._
- [x] **P2.6 — GND scenario generator.** Departure pushes + scripted Tower runway demand
  + arrivals exiting; difficulty bands. _Deps: P1.9, P2.1. Design §6.2, §12._
- [~] **P2.7 — Oracle controller policy (GND).** Heuristic controller for feasibility
  gating and the E-metric normalizer. Feasibility check rejects infeasible scenarios.
  _Audit: the gate (`baselines/feasibility.py`) is called only from tests — scenario
  generation never rejects-and-regenerates, and the oracle is not used as the E
  normalizer → P3.5.5, P3.5.6._ _Deps: P2.6. Design §12.2, §13.2._
- [~] **P2.8 — GND scorer.** Taxi delay vs. shortest-path, queue-order conformity,
  explicit-crossings log check, cardinal (RI-CTRL, DEADLOCK). _Audit: do-nothing scored
  S=1.0 gate=1 — fixed by P3.5.1 (NEGLECT cardinals, all-spawned denominators, H
  renormalized). Remaining: oracle-normalized E → P3.5.5._ _Deps: P2.4, P2.7.
  Design §6.2, §13._
- [x] **P2.9 — "Bad controller" busting policy + cert test.** Scripted bad policy that
  reliably busts; oracle certifies 3/3. Wire into CI. _Deps: P2.7, P2.8. Design §15 P2 exit._

---

## Phase 3 — Tower + token-metered regime

- [x] **P3.1 — Runway occupancy + wake-interval engine** (single-runway occupancy LoS + wake matrix; `sim/performance.py`) (§4.5, §6.3).
- [x] **P3.2 — Pattern/final kinematics (simplified) + go-around logic + LUAW** (1-D final closure, go-around with model/env provenance, line-up-and-wait) (§6.3).
- [x] **P3.3 — Token-metered regime accounting** (`sim_seconds = ceil(tokens / R)`, R=25;
  sim advances during "thinking"; retrofitted onto CD **and** GND; `--regime turn|metered|both`
  reports `tempo_gap`; metered runs replay byte-identically) (§4.2).
- [~] **P3.4 — TWR oracle + scorer** (conservative serialized oracle + feasibility gate; throughput
  vs. traffic, model-caused go-arounds via provenance; `atcbench run --position TWR`) (§6.3, §13.2).
  _Audit: do-nothing certified (S=0.65, gate=1) — fixed by P3.5.1 (NEGLECT cardinal,
  honest denominators, H renormalized). Remaining: oracle-normalized E + purposeful-set
  fix → P3.5.5._

---

## Phase 3.5 — Scorer & harness integrity (2026-07 audit fixes)

Fixes for the audit's critical/major scorer and elicitation findings. Blocks Phase 4:
TRACON/Center would inherit every defect below. **Exit test (P3.5.8):** the audit's
no-skill probes — a do-nothing controller and a blind re-clearance controller — run in
CI at every position and must **never** certify (extends the P2.9 falsification pattern).

- [x] **P3.5.1 — Neglect cardinals + honest denominators (audit C1).** Per-position
  NEGLECT-class cardinals at GND/TWR (unserviced aircraft past a threshold, arrivals
  never given a route, aircraft still active at `session_end`). E/A computed over **all
  spawned aircraft**, with unserviced aircraft scored 0. Empty aggregates with aircraft
  present default to 0, not 1.0 (`scoring/gnd.py`, `scoring/twr.py`). Unhardcode H at
  GND/TWR or renormalize weights over the components that exist at each position.
  _Done: GND NEGLECT (no taxi clearance in 180 s; stranded-at-hold-bar 300 s), TWR
  NEGLECT (never cleared, 180 s grace); all-spawned denominators; empty aggregates → 0
  with traffic present; H excluded + weights renormalized; do-nothing and
  strand-departures probes in CI at all positions._ _Deps: none. Design §13.1, §13.2._
- [x] **P3.5.2 — Hearback integrity (audit C2).** An error counts as *caught* only when
  (a) a scheduled error actually existed and (b) the correction addresses the erroneous
  element specifically. `receive_correction` (`pilots/fsm.py`) no longer sets
  `error_caught` unconditionally. Corrections issued after **correct** readbacks emit
  `spurious_correction` and dock F. Score the "readback correct" affirmation path so
  blanket-negative strategies are distinguishable from real hearback. _Done: H is now
  signal detection — catch rate on scheduled errors minus false-alarm rate on correct
  readbacks — so silence-after-correct is a correct accept and blanket-negative scores
  H=0 (blind corrector: S 1.0 → ≈0.69). BlindCDCorrector probe in CI (X.5)._
  _Deps: none. Design §6.1, §13.3._
- [ ] **P3.5.3 — Observation de-leakage (audit M1).** Remove `filing_error_hint` and the
  pre-parsed structured `last_readback` fields from observations — both are the skill
  under test, and the information is already available in the transcript + filed plan.
  Replace GND `next_hot_in` (a future-schedule oracle) with scripted Tower coordination
  transmissions; move TWR derived occupancy/wake fields (`since_last_use_sec`,
  `last_use_wake`) behind the enriched-representation track (P4.6). _Deps: none.
  Design §4.5, §11.2._
- [~] **P3.5.4 — Parser integrity (audit M5).** Numeric extraction in corrections scoped
  to element keywords — no cross-assignment ("negative, squawk four five zero zero" must
  never become altitude 4500 and corrupt pilot state). Tier-4 (unparseable) transmissions
  trigger a pilot `say_again` instead of a silent drop; `ParseTier` logged per
  transmission; model text arriving with no tool call logs `unparsed_model_output`
  instead of silently becoming `wait`. _Cross-assignment fixed with P3.5.2 (the altitude
  fallback was corrupting pilot state from the oracle's own squawk corrections;
  keyword-claimed spans now scrubbed first). Remaining: say_again, tier logging,
  unparsed_model_output; known edge — flight numbers divisible by 100 can still hit the
  altitude fallback._ _Deps: none. Design §7.2._
- [ ] **P3.5.5 — Oracle-normalized efficiency (audit M7).** Run the oracle per seed at
  score time and compute `E = clamp(model_metric / oracle_metric)` per §13.2, replacing
  the fixed absolute thresholds (which the oracle saturates on 97% of seeds). Fix the
  TWR "purposeful" set — `departed_sector` and go-around events are not model
  transmissions. _Deps: P3.5.1. Design §13.2._
- [ ] **P3.5.6 — Generation hygiene (audit m1, m5).** Wire `baselines/feasibility.py`
  into scenario generation as reject-and-regenerate per §12.2 (currently test-only).
  Exclude special-purpose squawks (7500/7600/7700) from assignment. _Deps: none.
  Design §12.2._
- [ ] **P3.5.7 — Replay compares all artifacts (audit m2, m4).** `replay` verifies
  transcript, strips history, and `score.json`, not just `events.jsonl`. Record the
  Python version in the run record; CI compares event logs across 3.11/3.12 (the matrix
  already runs both, it just never diffs them). _Deps: none. Folds into X.2.
  Design §17.2._
- [ ] **P3.5.8 — No-skill baseline regression suite (exit test; seeds X.5).**
  Do-nothing and blind-corrector policies as permanent CI busting probes at every
  position; must never certify. Grows with each new position, like the determinism
  suite. _Deps: P3.5.1, P3.5.2. Design §15 exit-test pattern._
- [ ] **P3.5.9 — Doc/claims alignment (audit m4).** Correct README certification wording
  and channel-physics claims (README §features + tool descriptions say transmissions
  cost time at all positions — true only at CD until P4.0a); fix stale comments
  (uniform vs "lumpy" call times; strip-op cost at CD only). Keep docs at the honesty
  standard of the fictional-facility flags. _Deps: P3.5.1–P3.5.5._
- [ ] **P3.5.10 — Route must be transmitted (audit M2, exploit-kill subset).**
  `_assign_route` fires only when the model's transmission contains a parseable,
  chart-legal route — bare "taxi" with no route gets a pilot "say again, request taxi
  route" instead of the free canonical route (verified exploit). Scorer checks the
  *transmitted text* against the chart pack's protocol rules (hold-short suffix present,
  crossings named explicitly) — pure log check, no sim change. Pilots still fly the
  route faithfully; misexecution/robustness stays in P4.0b. _Deps: P3.5.4. Design §6.2,
  §7.2._

---

## Phase 3.6 — Live-model path + statistics (gates any public number)

The benchmark has never scored a real LLM; certification is currently 3 seeds × 1 trial,
which a verified-unsafe policy passes ~2.6% of the time. **Exit test (P3.6.4):**
published run dirs in `runs/` from ≥2 real models, with bust-rate certification and
clustered CIs reported by `atcbench evaluate`.

- [ ] **P3.6.1 — System prompts for GND/TWR.** Extend P1.12's assembly to all three
  positions (only CD has one); `prompt_hash` per position. _Deps: P1.12. Design §11.3._
- [ ] **P3.6.2 — Live adapter end-to-end (audit C3, m6).** `--model` flag on `run`;
  real tool-result routing — `bay_read` returns actual bay contents (currently a no-op,
  making strips write-only for a live model); verbatim request/response logging in
  `model_io.json` (currently outputs only, contradicting §3.2); retry/backoff policy;
  `--max-usd` and per-session turn/token budgets (§17.4). _Deps: P3.6.1. Design §11.1,
  §17.1, §17.4, §3.2._
- [ ] **P3.6.3 — `evaluate` aggregator + certification statistics (audit M3).**
  Multi-seed × multi-trial runner per §17.1. Certification = per-session bust rate with
  a Wilson 95% upper bound below a pre-registered threshold over ≥30 sessions (replaces
  3/3-seeds, which is pass^3 at n=3). Report pass^k (k=3), session-clustered bootstrap
  CIs on S and components, and ICC across trials (T≥3 trials/seed for API models).
  _Deps: P3.6.2. Design §13.4, §17.1._
- [ ] **P3.6.4 — Pilot campaign + band calibration (exit test).** 2–3 models spanning
  ability × ≥20 seeds × ≥3 trials; publish run dirs in `runs/`; recalibrate difficulty
  bands so standard-band frontier cert-failure is informative (not 0% or 100%); only
  then pre-register weights/thresholds for any public leaderboard (P5.7). _Deps:
  P3.6.3. Design §12, §15._

---

## Phase 4 — TRACON + Generalist generator + full strip scoring

> **Blocked on Phases 3.5–3.6.** P4.0a–d close audit gaps in existing positions and
> should land before or alongside the TRACON build.

- [ ] **P4.0a — Shared frequency-channel physics (audit M4).** Lift CD's half-duplex
  time-cost/queueing channel into a shared component used by GND/TWR and all future
  positions; implement `[BLOCKED]`. Makes verbosity an operational cost everywhere
  (principle #3) and makes correction-spam self-punishing. Closes the P1.3 gap. (§7.1)
- [ ] **P4.0b — Pilots fly the transmitted route (audit M2, robustness remainder).**
  Builds on P3.5.10 (route production is already required and protocol-checked). GND
  pilots follow the route the model actually transmitted, with §7.2 tier-3
  misinterpretation of ambiguous routes; hold-short becomes instruction-driven (with
  readback closure) rather than structural, with the provenance/deadlock implications
  for the oracle and feasibility gate; route efficiency scored against the transmitted
  route. Closes the rest of the P2.3 gap. (§6.2, §7.2)
- [ ] **P4.0c — Error-class completion (audit m3).** Generator + FSM paths for CS-CONF
  (the flagship similar-callsign confusion class), SAY-AGAIN, and BLOCKED (needs P4.0a);
  TWR injection classes per §6.3; broader GND classes (with P2.5). Closes the P1.7 gap.
  (§8.3, §8.4)
- [ ] **P4.0d — Seed-drawn chart-pack constants (audit M6).** LOA initial altitude,
  frequencies, SID names/sets, and filing-error content drawn from seeded streams per
  scenario — currently the correct answers are seed-independent constants (always 5000,
  always 119.35, the only bad SID is literally "BOGUS9"), so a public repo means the
  answer key is memorizable regardless of seed rotation. Make some filing errors change
  the correct clearance. Contamination defense the Facility track needs even before the
  Generalist generator (P4.4). (§5.1, §5.2, §12.1)
- [ ] **P4.1 — Airborne vectoring kinematics** (point-mass, turn/climb/descent/speed
  rates, wind bands) (§4.3).
- [ ] **P4.2 — Approach-geometry validation** (intercept ≤ 30°, altitude-at-fix) (§6.4).
- [ ] **P4.3 — Stream-merge scenarios + TRACON scorer** (final spacing consistency, track
  miles vs. nominal, LOA handoff conformity) (§6.4).
- [ ] **P4.4 — Procedural airspace generator (Generalist track)** (seeded airport, fixes,
  STAR/SID/approaches, sector geometry, LOA; same chart-pack schema) (§5.2).
- [ ] **P4.5 — Full strip scoring** (fidelity/divergence, foresight/Kendall-τ, flag
  utility) (§9.4).
- [ ] **P4.6 — Raw vs. enriched representation tracks** (§11.2).

---

## Phase 5 — Center + endurance + human harness + leaderboard + presentation

- [ ] **P5.1 — High-count Center sector** (crossing flows, arrivals-to-gate descents,
  overflights) (§6.5).
- [ ] **P5.2 — Weather cells + deviation logic (DEV-WX)** (§6.5, §8.3).
- [ ] **P5.3 — 90-min endurance standard across all positions** (§4.1, §15).
- [ ] **P5.4 — EMERG + PROMPT-INJ handling & scoring** (EMERG-FAIL, INJ-COMPLY cardinals)
  (§8.3, §13.1). _Note: PROMPT-INJ appears from GND up — may pull earlier._
- [ ] **P5.5 — Full handoff protocol + scorecard** (inbound/outbound, clean-delivery,
  orphan detection) (§10). _Note: partially needed from TWR/TRACON — may pull earlier._
- [ ] **P5.6 — Human TUI baseline harness** (§14).
- [ ] **P5.7 — Public leaderboard + run-record publication format** (§13.4, §17.3).
- [ ] **P5.8 — Replay scope (web viewer)** (§18.1).
- [ ] **P5.9 — Auto-generated incident reports** (§18.2).
- [ ] **P5.10 — Capacity ramp mode** (§18.3).
- [ ] **P5.11 — Public play harness** (§18.4).

---

## Cross-cutting / ongoing

- [~] **X.1 — Aircraft performance table.** _Started (`sim/performance.py`: approach speed, wake, climb; used by Tower)._  Pinned per-type table (B738, A320, C172,
  E175, B77W, …): min/max IAS by phase, climb/descent rates, turn rate, wake category.
  OpenAP-seeded, shipped pinned. _Needed from Phase 3; stub earlier._ (§3.2)
- [ ] **X.2 — Determinism regression suite.** Grow the replay/byte-identical test as
  each position lands. Includes replay of **all** run artifacts and cross-Python-version
  log comparison (see P3.5.7). (§17.2)
- [ ] **X.3 — Parser variant corpus.** Grow the real-world phraseology corpus that gates
  parser behavior. Prerequisite for any real-model campaign (see P3.5.4). (Key risk #1)
- [ ] **X.4 — Versioning discipline.** prompt_hash, harness_version, parser version,
  weight constants (R, scoring weights) pinned; changes are major version bumps. Record
  Python version in run records. (§11.4, §13.2, §4.2)
- [ ] **X.5 — No-skill baseline regression suite.** Do-nothing and blind-corrector
  probes must never certify at any position; grows with each new position exactly like
  X.2 grows for determinism. (Seeded by P3.5.8; 2026-07 audit.)

---

## Open questions to resolve during build (non-blocking — DESIGN §16)

1. Correction-window length per position (proposal: until compliance begins or 30 s).
2. `bay_read` auto-injected each turn vs. on-demand (proposal: on-demand).
3. CA/MSAW assisted-variant column (v1: no).
4. Verbalizer model choice (small/cheap/pinnable, persona at temp 0).
5. Generalist-track rule-surface randomization (deferred).

---

## Suggested starting order

For a first working demo, the shortest path to something runnable:
**P0.1 → P0.3 → P0.4 → P1.1 → P1.5 → P1.6 → P1.8 → P1.9 → P1.10 → P1.11 → P1.14 → P1.13.**
That yields an end-to-end deterministic CD session with a scorer and the exit test.
