# ATCBench

A closed-loop, agentic benchmark that evaluates whether an LLM can do the job of an
air traffic controller. The model works a live simulated position: it receives radar
state and radio transcript as text, issues instructions in ICAO/FAA phraseology,
manages flight strips via tools, and is **scored deterministically** from the event
log — no LLM judges, ever.

See **[DESIGN.md](./DESIGN.md)** for the full specification and **[TASKS.md](./TASKS.md)**
for the work breakdown. Progress is tracked in the GitHub issues (one epic per phase).

## Status

Phases 1–3 — deterministic **Clearance Delivery**, **Ground**, and **Tower** positions
(the ladder is CD → GND → TWR), both time regimes, full scoring, certification gating,
and a byte-identical replay determinism check. A 2026-07 benchmark audit drove a
scorer/harness integrity pass (Phase 3.5, issue #14): no-skill baselines now bust on
NEGLECT everywhere, hearback is signal detection (blind re-clearing scores H=0),
observations carry no ground truth or pre-computed skill, and E/A are normalized
against the oracle on the same seed. **The live-model path is not built yet** (issue
#15): only scripted controllers run today, so no real-LLM numbers exist or should be
quoted.

> **Facility honesty note.** v1 runs on a **fictional stand-in airport — Marlow
> Regional (KMRL)** — whose runways, taxi graph, SIDs, frequencies, and LOA are
> *fabricated* (flagged in-code via `FACILITY_KIND = "fictional"`). It is a
> Generalist-style pack wearing a facility nameplate; it does not yet exercise the
> Facility-track premise of a real airport the model may know from training. Parsing
> real FAA charts (KMDW, C90, ZAU) into facility packs is future work — see DESIGN §5.1.

Implemented so far:

- Named-PRNG seed manager (all randomness flows from one master seed) — `scenarios/seeds.py`
- Append-only JSONL event log — `sim/events.py`
- Tiered phraseology parser with number normalization — `pilots/parser.py`
- Per-aircraft pilot FSM (CD subset) + seeded error schedule — `pilots/fsm.py`, `scenarios/cd.py`
- Deterministic template verbalizer + response cache, pluggable LLM backend — `verbalizer/`
- Half-duplex frequency channel (150 wpm broadcast physics) — **CD only** for now;
  GND/TWR transmissions are free until the shared channel component lands (P4.0a) —
  `harness/session.py`
- Taxi graph + Ground position: taxi kinematics, explicit crossings, runway-incursion
  and head-on deadlock oracle — `sim/taxi.py`, `charts/kmrl_gnd.py`, `harness/ground_session.py`
- Model adapter + tool router: scripted oracle & bad controllers at all three
  positions, no-skill audit probes (do-nothing, blind-corrector, routeless-taxi,
  strand-departures), and a replay adapter — `harness/adapters.py`. (An Anthropic
  adapter exists but is **not yet wired to the CLI** — see issue #15.)
- Flight strip store + tools — `strips/store.py`
- CD + GND + TWR scorers (oracle-normalized E/A, NEGLECT cardinals, hearback signal
  detection), feasibility gate wired into generation — `scoring/`, `baselines/`
- CLI: `run --position CD|GND|TWR` / `score` / `replay` (verifies **all** artifacts) —
  `cli.py`

## Quickstart

```bash
pip install -e '.[dev]'

# Run a CD session with the deterministic oracle controller (no API key needed)
python -m atcbench.cli run --seed 42 --band standard --controller scripted --out runs/oracle_s42

# Re-score any run directory from its log alone (pure function; no model calls)
python -m atcbench.cli score runs/oracle_s42

# Determinism contract: replay the recorded model outputs; event log must be byte-identical
python -m atcbench.cli replay runs/oracle_s42 --out runs/replay_check

# Watch a "bad controller" (never closes the readback loop) bust on a safety-critical error
python -m atcbench.cli run --seed 42 --controller bad

# Run a Ground session (taxi graph, explicit crossings, incursion + deadlock oracle)
python -m atcbench.cli run --position GND --seed 42

# Both time regimes side by side; reports tempo_gap = S(turn) - S(metered)
python -m atcbench.cli run --seed 42 --regime both

pytest -q   # determinism, parser, scoring, and regime tests
```

## Time regimes (DESIGN §4.2)

Two regimes share the same sim; only how the model's *thinking* is charged differs:

- **`turn`** — the clock freezes while the model reasons (pure decision quality).
- **`metered`** — the model's output consumes sim time at `R = 25` tokens/sim-second
  (`sim_seconds = ceil(output_tokens / R)`); the world keeps moving while it thinks, so a
  verbose model literally falls behind the traffic and can watch a correction window close.

Both are reported as separate columns; `tempo_gap = S(turn) − S(metered)`. Metered runs
replay byte-identically (the recorded token counts drive the accounting).

## How scoring works

Each session yields `S = gate · S_raw`, with `S_raw` a weighted blend of components
(0.35·E + 0.25·H + 0.20·F + 0.20·A at CD; positions without readback error classes
yet exclude H and renormalize the remaining weights — no free credit):

- **gate** — 0 on any *cardinal* violation: an uncorrected wrong altitude departing,
  a model-caused runway incursion, LoS or wake bust, **or NEGLECT** (aircraft never
  serviced — inaction is a cardinal, so a do-nothing controller scores 0 everywhere).
- **E** efficiency and **A** attention — normalized **per aircraft against the
  scripted oracle worked on the same seed** (§13.2): the oracle defines 1.0, not an
  arbitrary threshold.
- **H** hearback — signal detection: catch rate on scheduled readback errors *minus*
  false-alarm rate on correct readbacks. Blind "negative…" re-clearing of every
  readback catches everything, false-alarms everything, and scores exactly 0.
- **F** frequency & protocol discipline — purposeful transmissions over all
  transmissions; spurious corrections and protocol violations (e.g. a crossing route
  without its hold-short) don't count as purposeful.

The bundled oracle certifies with S=1.0 on every seed and band; the bad controllers
and the no-skill probes (`tests/test_no_skill_probes.py`) bust by construction — that
falsification suite is CI-enforced and grows with every position.

**Certification is statistical** (`atcbench evaluate`): per-session bust rate with a
Wilson 95% upper bound below 5%, minimum 30 sessions — honest math: 30 clean sessions
still bound at ~11%, so certifying takes ~75+ clean sessions. Reported alongside
pass@1, pass^k (all trials of a seed pass), session-clustered bootstrap CIs on S, and
ICC across trials. Until a real model has been run end-to-end (the Phase 3.6 pilot
campaign, issue #15), no headline numbers should be quoted from this benchmark.

## Determinism

The **environment** — pilots, physics, error injection, scoring — is a pure function
of `(seed, model_outputs)`. The model under test need not be deterministic; the replay
check records model outputs and asserts the event log is reproducible byte-for-byte.

## License

Apache-2.0.
