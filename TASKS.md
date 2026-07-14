# ATCBench — Task Breakdown

Work breakdown derived from [DESIGN.md](./DESIGN.md) §15 (build plan). Tasks are grouped
by phase and ordered by dependency. **v1 public release = Phases 1–2.**

Legend: `[ ]` todo · `[~]` in progress · `[x]` done. Each task lists the design
section(s) it implements and its hard dependencies.

> **Status (2026-07-14):** Positions CD → GND → TWR are live with both time regimes,
> shared half-duplex channel physics, seeded per-scenario chart packs, the full v1
> error taxonomy subset (incl. CS-CONF/SAY-AGAIN/BLOCKED + GND/TWR injections), and
> realistic fleet/callsign pairing. 149 tests, ruff clean, byte-identical replay
> (verified on live-model runs), cross-Python-version determinism in CI.
>
> **2026-07 benchmark audit** (issues #14/#15): all findings fixed. **Phase 3.5**
> (scorer/harness integrity) is complete and closed — no-skill probes are permanent CI
> (X.5). **Phase 3.6**: prompts, live adapter (`run --model`), and statistical
> `evaluate` (Wilson-bound certification, pass^k, clustered CIs) are done; **P3.6.4 is
> half-done** — the small pilot (26 live sessions, `runs/pilot/`) proved every axis
> discriminates; the **full campaign (≥20 seeds × ≥3 trials, ~$150–250) is the last
> step before public numbers**. The **P4.0 pre-campaign construct batch (a–e) is
> complete**, so campaign baselines land on a stable construct. Calibration probes in
> `runs/probes/` (TWR calm band shows a gradient: the standard-band wall opens up).
> Next after the campaign: pre-register weights, close #15, then TRACON (P4.1+).

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
- [x] **P1.3 — Frequency channel model.** Half-duplex single channel; broadcast duration
  `ceil(word_count / 2.5)`; transmission queueing when channel busy; `[BLOCKED]` handling
  stub. _Audit gap closed by P4.0a: shared `FrequencyChannel` at all positions, with
  `[BLOCKED]` implemented at the time-stepped positions._ _Deps: P1.1. Design §7.1._
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
- [x] **P1.7 — Error schedule (CD subset).** Seeded per-aircraft error schedule for CD
  classes: RB-ALT, RB-HDG, RB-FREQ, RB-DROP, RB-PART, CS-CONF, CS-WRONG, SAY-AGAIN,
  BLOCKED. _Audit gap closed by P4.0c: CS-CONF, SAY-AGAIN, BLOCKED now have full
  generator + FSM paths. RB-HDG deferred to P4.1 by design — there are no headings at
  CD; it becomes meaningful with TRACON kinematics._ _Deps: P0.4, P1.6. Design §8.3._
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
- [x] **P1.11 — Model adapter + tool router.** Provider-agnostic adapter (Anthropic
  Messages tool schema native; OpenAI-style adapter). Tools for CD: `transmit`, strip
  tools, `wait`. State serializer, verbatim I/O logging, token-count capture. _Audit
  gap closed by P3.6.2: live adapter wired end-to-end with real tool results,
  retries, budgets, and verbatim I/O; multi-provider adapters tracked as X.6._
  _Deps: P1.2, P1.4. Design §11.1, §11.4, §3.2._
- [x] **P1.12 — System-prompt assembly.** Versioned template assembling the 9 §11.3
  sections from the chart pack; emit `prompt_hash` into the run record. _Deps: P1.10.
  Design §11.3._

### Scoring
- [x] **P1.14 — CD scorer.** Pure function of the log. Cardinal detection (NEGLECT,
  uncorrected bad readback on departed queue item), severe cap, S_raw components with
  CD-specific E (service time). Hearback H from RB-*/CS-* catch rate. _Audit gaps
  closed: hearback integrity by P3.5.2, oracle-normalized E/A by P3.5.5._ _Deps: P1.2.
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
- [x] **P2.3 — Ground clearance parsing.** Taxi-route + explicit-crossing + hold-short
  phraseology (no implied crossings). Extends the parser. _Audit gaps closed: route
  production required + protocol-checked by P3.5.10; pilots fly the transmitted route
  by P4.0b._ _Deps: P1.5, P2.1. Design §6.2._
- [x] **P2.4 — Incursion & deadlock detection.** Runway incursion (RI-CTRL provenance:
  model clearance vs. injected FSM error) and head-on deadlock oracle. _Deps: P2.1.
  Design §4.4, §4.5, §13.1._
- [~] **P2.5 — Ground FSM states & error subset.** Wrong-turn compliance, dropped
  hold-short readback, hot-spot stop, progressive-taxi (`unfamiliar` persona).
  _Wrong-turn landed with P4.0c (GND-WRONG-TURN); hold-short drop existed (GND-HS-DROP).
  Remaining: hot-spot stop, progressive taxi._ _Deps: P1.6, P2.1. Design §6.2, §8.3._
- [x] **P2.6 — GND scenario generator.** Departure pushes + scripted Tower runway demand
  + arrivals exiting; difficulty bands. _Deps: P1.9, P2.1. Design §6.2, §12._
- [x] **P2.7 — Oracle controller policy (GND).** Heuristic controller for feasibility
  gating and the E-metric normalizer. Feasibility check rejects infeasible scenarios.
  _Audit gaps closed: oracle as E-normalizer by P3.5.5; reject-and-regenerate wired
  into generation by P3.5.6._ _Deps: P2.6. Design §12.2, §13.2._
- [x] **P2.8 — GND scorer.** Taxi delay vs. oracle baseline, queue-order conformity,
  explicit-crossings log check, cardinal (RI-CTRL, DEADLOCK). _Audit gaps closed:
  NEGLECT cardinals + honest denominators by P3.5.1, oracle-normalized E/A by
  P3.5.5._ _Deps: P2.4, P2.7. Design §6.2, §13._
- [x] **P2.9 — "Bad controller" busting policy + cert test.** Scripted bad policy that
  reliably busts; oracle certifies 3/3. Wire into CI. _Deps: P2.7, P2.8. Design §15 P2 exit._

---

## Phase 3 — Tower + token-metered regime

- [x] **P3.1 — Runway occupancy + wake-interval engine** (single-runway occupancy LoS + wake matrix; `sim/performance.py`) (§4.5, §6.3).
- [x] **P3.2 — Pattern/final kinematics (simplified) + go-around logic + LUAW** (1-D final closure, go-around with model/env provenance, line-up-and-wait) (§6.3).
- [x] **P3.3 — Token-metered regime accounting** (`sim_seconds = ceil(tokens / R)`, R=25;
  sim advances during "thinking"; retrofitted onto CD **and** GND; `--regime turn|metered|both`
  reports `tempo_gap`; metered runs replay byte-identically) (§4.2).
- [x] **P3.4 — TWR oracle + scorer** (conservative serialized oracle + feasibility gate; throughput
  vs. traffic, model-caused go-arounds via provenance; `atcbench run --position TWR`) (§6.3, §13.2).
  _Audit gaps closed: NEGLECT cardinal + honest denominators by P3.5.1,
  oracle-normalized E/A + purposeful-set fix by P3.5.5._

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
- [x] **P3.5.3 — Observation de-leakage (audit M1).** Remove `filing_error_hint` and the
  pre-parsed structured `last_readback` fields from observations — both are the skill
  under test, and the information is already available in the transcript + filed plan.
  Replace GND `next_hot_in` (a future-schedule oracle) with scripted Tower coordination
  transmissions; move TWR derived occupancy/wake fields (`since_last_use_sec`,
  `last_use_wake`) behind the enriched-representation track (P4.6). _Done: CD oracle
  now works hearback from the frequency feed alone (parses pilot radio calls, hears
  RB-DROP as silence) and still catches 12/12 at heavy band; Tower coordination calls
  ("hold all crossings" / "at your discretion") announce hot windows at GND;
  `TowerSession(representation="raw"|"enriched")` stubs the P4.6 split, with the TWR
  oracle keeping its own runway-use picture. Observation contract pinned in
  `tests/test_observation.py`._ _Deps: none. Design §4.5, §11.2._
- [x] **P3.5.4 — Parser integrity (audit M5).** Numeric extraction in corrections scoped
  to element keywords — no cross-assignment ("negative, squawk four five zero zero" must
  never become altitude 4500 and corrupt pilot state). Tier-4 (unparseable) transmissions
  trigger a pilot `say_again` instead of a silent drop; `ParseTier` logged per
  transmission; model text arriving with no tool call logs `unparsed_model_output`
  instead of silently becoming `wait`. _Done: cross-assignment fixed with P3.5.2;
  `controller_parse` (tier + intent) logged for every controller transmission at all
  three positions; addressed-but-unusable transmissions draw a pilot "say again" on
  frequency; formatting failures logged verbatim. Known edge for X.3: flight numbers
  divisible by 100 can still hit the altitude fallback; GND/TWR tiers are coarse until
  the variant corpus lands._ _Deps: none. Design §7.2._
- [x] **P3.5.5 — Oracle-normalized efficiency (audit M7).** Run the oracle per seed at
  score time and compute `E = clamp(model_metric / oracle_metric)` per §13.2, replacing
  the fixed absolute thresholds (which the oracle saturates on 97% of seeds). Fix the
  TWR "purposeful" set — `departed_sector` and go-around events are not model
  transmissions. _Done: all three scorers regenerate the scenario from (seed, band,
  session_seconds), run the oracle (cached), and normalize E/A per aircraft against
  it — the oracle scores exactly 1.0 by construction (TWR A was silently capped at
  ~0.42 by the unreachable 60 s threshold). TWR go-arounds carry a `commanded` flag:
  only commanded ones are purposeful, and only go-arounds in excess of the oracle's
  count against E. CD affirmations of pending readbacks (`readback_affirmed`) count
  as purposeful._ _Deps: P3.5.1. Design §13.2._
- [x] **P3.5.6 — Generation hygiene (audit m1, m5).** Wire `baselines/feasibility.py`
  into scenario generation as reject-and-regenerate per §12.2 (currently test-only).
  Exclude special-purpose squawks (7500/7600/7700) from assignment. _Done: GND/TWR
  `generate()` rejects infeasible candidates and deterministically rerolls (seed +
  attempt·stride), recording the seed actually used so run-record regeneration is a
  fixed point; feasibility checks inspect the oracle's event log directly (calling the
  scorer would recurse through §13.2 normalization). CD scenarios are feasible by
  construction (no spatial conflicts; all errors catchable in-window). Special squawks
  excluded and pinned by test._ _Deps: none. Design §12.2._
- [x] **P3.5.7 — Replay compares all artifacts (audit m2, m4).** `replay` verifies
  transcript, strips history, and `score.json`, not just `events.jsonl`. Record the
  Python version in the run record; CI compares event logs across 3.11/3.12 (the matrix
  already runs both, it just never diffs them). _Done: `atcbench replay` re-scores the
  replay and diffs all four artifacts (per-file verdicts printed); `python_version` in
  every `model_io.json`; new `cross-version-determinism` CI job runs CD/GND/TWR on
  both interpreters and byte-compares artifacts._ _Deps: none. Folds into X.2.
  Design §17.2._
- [x] **P3.5.8 — No-skill baseline regression suite (exit test; seeds X.5).**
  Do-nothing and blind-corrector policies as permanent CI busting probes at every
  position; must never certify. Grows with each new position, like the determinism
  suite. _Done: `tests/test_no_skill_probes.py` is the X.5 suite — DoNothing busts on
  NEGLECT at all three positions, TaxiOnlyGND busts on stranded-NEGLECT, BlindCD
  scores H=0 and sits strictly below the oracle (its gate-level cert failure lands
  with P3.6.3 bust-rate certification). Probes live in `harness/adapters.py`; every
  new position must add its probes to the suite. **Phase 3.5 exit test: passing.**_
  _Deps: P3.5.1, P3.5.2. Design §15 exit-test pattern._
- [x] **P3.5.9 — Doc/claims alignment (audit m4).** Correct README certification wording
  and channel-physics claims (README §features + tool descriptions say transmissions
  cost time at all positions — true only at CD until P4.0a); fix stale comments
  (uniform vs "lumpy" call times; strip-op cost at CD only). Keep docs at the honesty
  standard of the fictional-facility flags. _Done: README status/scoring/certification
  rewritten to match audited reality (incl. "no headline numbers until a real model
  runs"); channel physics flagged CD-only; seeds.py cross-version claim softened and
  pointed at the CI job; stale comments fixed. The `transmit` tool description lives
  in CD_TOOLS only, where the claim is true._ _Deps: P3.5.1–P3.5.5._
- [x] **P3.5.10 — Route must be transmitted (audit M2, exploit-kill subset).**
  `_assign_route` fires only when the model's transmission contains a parseable,
  chart-legal route — bare "taxi" with no route gets a pilot "say again, request taxi
  route" instead of the free canonical route (verified exploit). Scorer checks the
  *transmitted text* against the chart pack's protocol rules (hold-short suffix present,
  crossings named explicitly) — pure log check, no sim change. Pilots still fly the
  route faithfully; misexecution/robustness stays in P4.0b. _Done: departures need
  runway + via-alpha, arrivals need a via (alpha = misroute, flown as transmitted);
  routeless taxi draws a "say again the route" and ends in NEGLECT
  (NoRouteGNDController probe in X.5). Taxi clearances on the crossing taxiway missing
  the hold-short are reported `missing_hold_short` and excluded from purposeful F._
  _Deps: P3.5.4. Design §6.2, §7.2._

---

## Phase 3.6 — Live-model path + statistics (gates any public number)

The benchmark has never scored a real LLM; certification is currently 3 seeds × 1 trial,
which a verified-unsafe policy passes ~2.6% of the time. **Exit test (P3.6.4):**
published run dirs in `runs/` from ≥2 real models, with bust-rate certification and
clustered CIs reported by `atcbench evaluate`.

- [x] **P3.6.1 — System prompts for GND/TWR.** Extend P1.12's assembly to all three
  positions (only CD has one); `prompt_hash` per position. _Done:
  `build_system_prompt(position, ...)` returns (text, versioned hash) for CD/GND/TWR;
  GND teaches the coordination protocol and route-required rule, TWR carries the wake
  matrix + own-picture obligation; CD prompt bumped to cd-v2 (hearback/false-alarm
  semantics). All sessions record the real hash._ _Deps: P1.12. Design §11.3._
- [x] **P3.6.2 — Live adapter end-to-end (audit C3, m6).** `--model` flag on `run`;
  real tool-result routing — `bay_read` returns actual bay contents (currently a no-op,
  making strips write-only for a live model); verbatim request/response logging in
  `model_io.json` (currently outputs only, contradicting §3.2); retry/backoff policy;
  `--max-usd` and per-session turn/token budgets (§17.4). _Done: sessions return one
  result string per tool call and push them via `adapter.receive_tool_results`;
  AnthropicAdapter attaches them to the next request in a single alternating user
  message, retries transient API errors with backoff, and enforces `--max-usd`
  (explicit `--usd-per-mtok-in/out`, no baked price table) by waiting out the session
  with `budget_exhausted` flagged in every turn + score.json. `model_io.json` turns
  carry observation, output, and tool_results. Fake-client tests pin conversation
  shape, retry, and budget behavior._ _Deps: P3.6.1. Design §11.1, §17.1, §17.4, §3.2._
- [x] **P3.6.3 — `evaluate` aggregator + certification statistics (audit M3).**
  Multi-seed × multi-trial runner per §17.1. Certification = per-session bust rate with
  a Wilson 95% upper bound below a pre-registered threshold over ≥30 sessions (replaces
  3/3-seeds, which is pass^3 at n=3). Report pass^k (k=3), session-clustered bootstrap
  CIs on S and components, and ICC across trials (T≥3 trials/seed for API models).
  _Done: `atcbench evaluate` + `scoring/aggregate.py` — Wilson-bounded bust rate
  (pre-registered: upper < 5%, n ≥ 30; note 30 clean sessions bound at ~11%, so
  certification realistically needs ~75+), pass@1, pass-all-trials, seed-clustered
  bootstrap CI on mean S, ICC(1) across trials, per-seed detail; writes run dirs +
  summary.json. Statistics pinned by tests, including the audit's 70%-bust
  controller never approaching certification._ _Deps: P3.6.2. Design §13.4, §17.1._
- [~] **P3.6.4 — Pilot campaign + band calibration (exit test).** 2–3 models spanning
  ability × ≥20 seeds × ≥3 trials; publish run dirs in `runs/`; recalibrate difficulty
  bands so standard-band frontier cert-failure is informative (not 0% or 100%); only
  then pre-register weights/thresholds for any public leaderboard (P5.7). _Small pilot
  done (runs/pilot, 26 sessions, $20): every axis discriminates — Haiku < Sonnet at
  every cell; CD standard saturated / heavy informative (pass@1 0.6 vs 0.8 turn);
  metering costs both models ~0.2 S and halves Haiku's cert rate; GND separates
  (Haiku 1/3 vs Sonnet 1/1 at S=1.0); TWR busts both (wake+neglect vs one neglected
  go-around loop). All busts audited as legitimate model failures. Learned: TWR spawn
  schedule requires full-length sessions (feasibility gate correctly rejects 1200 s).
  Remaining: full campaign (≥20 seeds × ≥3 trials) + band recalibration._ _Deps:
  P3.6.3. Design §12, §15._

---

## Phase 4 — TRACON + Generalist generator + full strip scoring

> **Blocked on Phases 3.5–3.6.** P4.0a–d close audit gaps in existing positions and
> should land before or alongside the TRACON build.

- [x] **P4.0a — Shared frequency-channel physics (audit M4).** Lift CD's half-duplex
  time-cost/queueing channel into a shared component used by GND/TWR and all future
  positions; implement `[BLOCKED]`. Makes verbosity an operational cost everywhere
  (principle #3) and makes correction-spam self-punishing. Closes the P1.3 gap. (§7.1)
  _Done: `harness/channel.py` FrequencyChannel — CD keeps queue-then-advance semantics
  (byte-identical determinism preserved); at GND/TWR all speakers queue on the channel
  and a model transmit over a busy channel is `[BLOCKED]` (event + tool result) and
  forfeits the sweep's action window. `channel_busy` exposed in observations (audible
  reality); oracles/bad controllers wait for a clear channel; prompts (gnd-v2/twr-v2),
  tool descriptions, and README updated; `blocked_transmissions` in scorer counts._
- [x] **P4.0b — Pilots fly the transmitted route (audit M2, robustness remainder).**
  Builds on P3.5.10 (route production is already required and protocol-checked). GND
  pilots follow the route the model actually transmitted, with §7.2 tier-3
  misinterpretation of ambiguous routes; hold-short becomes instruction-driven (with
  readback closure) rather than structural, with the provenance/deadlock implications
  for the oracle and feasibility gate; route efficiency scored against the transmitted
  route. Closes the rest of the P2.3 gap. (§6.2, §7.2) _Done: taxi edges carry taxiway
  labels; `build_route` BFS-constructs the pilot's path from the *transmitted* via set
  and destination (`TaxiGraph.route_via`) — wrong taxiways don't connect (say again),
  misroutes and wrong hold bars are flown as said, wrong destinations park and never
  count as arrived (E + stranded-NEGLECT), and a parked aircraft can be re-routed from
  where it stands. Misheard/unknown taxiways are dropped (tier-3-style partial
  resolution). Scope note: runway hold bars remain structural — FAA-correct (no
  crossing without explicit clearance); instruction-driven holds await charts with
  non-runway hold points; GND-HS-DROP already covers dropped hold-short readbacks._
- [x] **P4.0c — Error-class completion (audit m3).** Generator + FSM paths for CS-CONF
  (the flagship similar-callsign confusion class), SAY-AGAIN, and BLOCKED (needs P4.0a);
  TWR injection classes per §6.3; broader GND classes (with P2.5). Closes the P1.7 gap.
  (§8.3, §8.4) _Done: CS-CONF — the twin intercepts a clearance addressed to its base,
  reads it back under its own callsign, flies it uncaught (cardinal) or reverts on any
  in-window re-address; trigger-aware catchable counting means clearing the twin first
  legitimately evaporates the error. SAY-AGAIN — pilot requests a repeat, stays
  uncleared (neglect clock refreshed), caught by re-transmitting. BLOCKED — readback
  stepped on ([BLOCKED] noise on frequency), caught by re-prompting. GND-WRONG-TURN —
  arrival flies A when cleared via B (pilot deviation; recoverable via crossing +
  sequencing; oracle crossing rule generalized to any held aircraft). TWR-SLOW-EXIT
  and TWR-LUAW-MISS per §6.3. Oracle re-prioritized: pending readbacks judged before
  new clearances (corrections race a 30 s window; clearances have 180 s) — found when
  a CS-CONF disregard lost the race under heavy load. Scope notes: RB-HDG → P4.1;
  COMP-*/REQ-*/NORDO/DEV-WX/EMERG/PROMPT-INJ remain P5-era classes; H stays CD-only
  (GND/TWR classes are deviation/monitoring, not hearback)._
- [x] **P4.0e — Realistic fleet/callsign pairing.** GA types (C172) currently fly
  under airline callsigns (pilot campaign produced "American 4143" as a Cessna 172).
  Assign N-number registrations to GA types (verbalizer/parser already carry partial
  N-number support), airline callsigns only to airliner types, and make the type mix
  per role/band plausible. Changes the seeded callsign streams → score-perturbing, so
  it lands with this batch, before the full campaign. (§5.1, §8.1) _Done:
  `scenarios/fleet.py` pairs callsign style to airframe at all three positions
  (N + 3 digits + 2 letters, no I/O); GA airframes get GA voices at CD
  (ga_relaxed/student_pilot); similar-callsign twins are airline-only; full NATO
  telephony for registrations ("November seven one four kilo charlie");
  extract_callsign hardened so single-letter prefixes can't soak up unrelated text._
- [x] **P4.0d — Seed-drawn chart-pack constants (audit M6).** LOA initial altitude,
  frequencies, SID names/sets, and filing-error content drawn from seeded streams per
  scenario — currently the correct answers are seed-independent constants (always 5000,
  always 119.35, the only bad SID is literally "BOGUS9"), so a public repo means the
  answer key is memorizable regardless of seed rotation. Make some filing errors change
  the correct clearance. Contamination defense the Facility track needs even before the
  Generalist generator (P4.4). (§5.1, §5.2, §12.1) _Done: `CDChartPack` drawn from the
  `airspace` stream per scenario — frequency, 3–5 fictional SIDs with per-SID LOA
  altitudes (3000–7000), and a seeded invalid-filing fallback rule stated in the
  chart. Filing errors now change the answer (invalid SID → fallback route AND its
  LOA altitude); RB-ALT/RB-FREQ error content is pack-relative. Pack ships in
  scenario.json + the system prompt (cd-v4; hash varies per seed by design); oracle
  gets it via a `brief()` position-briefing hook; scorer/parser read the scenario
  pack. Pinned by `tests/test_chart_pack.py`._
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
- [ ] **X.6 — Multi-provider adapters.** OpenAI/Gemini/Grok adapters behind the same
  `ModelAdapter` interface (tool-result plumbing already provider-agnostic), so the
  leaderboard can compare across labs. Not needed for the Anthropic pilot campaign
  (P3.6.4); needed before any cross-lab claims. (§11.1)

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
