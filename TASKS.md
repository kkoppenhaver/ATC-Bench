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
> FAA chart packs are future work); P2.5 (one ground error class so far). 47 tests, ruff clean.

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
  stub. _Deps: P1.1. Design §7.1._
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
  BLOCKED. _Deps: P0.4, P1.6. Design §8.3._
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
  tools, `wait`. State serializer, verbatim I/O logging, token-count capture. _Deps:
  P1.2, P1.4. Design §11.1, §11.4, §3.2._
- [x] **P1.12 — System-prompt assembly.** Versioned template assembling the 9 §11.3
  sections from the chart pack; emit `prompt_hash` into the run record. _Deps: P1.10.
  Design §11.3._

### Scoring
- [x] **P1.14 — CD scorer.** Pure function of the log. Cardinal detection (NEGLECT,
  uncorrected bad readback on departed queue item), severe cap, S_raw components with
  CD-specific E (service time). Hearback H from RB-*/CS-* catch rate. _Deps: P1.2.
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
  phraseology (no implied crossings). Extends the parser. _Deps: P1.5, P2.1. Design §6.2._
- [x] **P2.4 — Incursion & deadlock detection.** Runway incursion (RI-CTRL provenance:
  model clearance vs. injected FSM error) and head-on deadlock oracle. _Deps: P2.1.
  Design §4.4, §4.5, §13.1._
- [~] **P2.5 — Ground FSM states & error subset.** Wrong-turn compliance, dropped
  hold-short readback, hot-spot stop, progressive-taxi (`unfamiliar` persona). _Deps:
  P1.6, P2.1. Design §6.2, §8.3._
- [x] **P2.6 — GND scenario generator.** Departure pushes + scripted Tower runway demand
  + arrivals exiting; difficulty bands. _Deps: P1.9, P2.1. Design §6.2, §12._
- [x] **P2.7 — Oracle controller policy (GND).** Heuristic controller for feasibility
  gating and the E-metric normalizer. Feasibility check rejects infeasible scenarios.
  _Deps: P2.6. Design §12.2, §13.2._
- [x] **P2.8 — GND scorer.** Taxi delay vs. shortest-path, queue-order conformity,
  explicit-crossings log check, cardinal (RI-CTRL, DEADLOCK). _Deps: P2.4, P2.7.
  Design §6.2, §13._
- [x] **P2.9 — "Bad controller" busting policy + cert test.** Scripted bad policy that
  reliably busts; oracle certifies 3/3. Wire into CI. _Deps: P2.7, P2.8. Design §15 P2 exit._

---

## Phase 3 — Tower + token-metered regime

- [ ] **P3.1 — Runway occupancy + wake-interval engine** (Design §4.5, §6.3).
- [ ] **P3.2 — Pattern/final kinematics (simplified) + go-around logic + LUAW** (§6.3).
- [ ] **P3.3 — Token-metered regime accounting** (`sim_seconds = ceil(tokens / R)`, R=25;
  sim advances during "thinking") (§4.2).
- [ ] **P3.4 — TWR oracle + scorer** (throughput vs. feasible max, model-caused go-arounds
  via event provenance) (§6.3, §13.2).

---

## Phase 4 — TRACON + Generalist generator + full strip scoring

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

- [ ] **X.1 — Aircraft performance table.** Pinned per-type table (B738, A320, C172,
  E175, B77W, …): min/max IAS by phase, climb/descent rates, turn rate, wake category.
  OpenAP-seeded, shipped pinned. _Needed from Phase 3; stub earlier._ (§3.2)
- [ ] **X.2 — Determinism regression suite.** Grow the replay/byte-identical test as
  each position lands. (§17.2)
- [ ] **X.3 — Parser variant corpus.** Grow the real-world phraseology corpus that gates
  parser behavior. (Key risk #1)
- [ ] **X.4 — Versioning discipline.** prompt_hash, harness_version, parser version,
  weight constants (R, scoring weights) pinned; changes are major version bumps. (§11.4,
  §13.2, §4.2)

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
