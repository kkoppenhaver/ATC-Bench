# ATCBench

A closed-loop, agentic benchmark that evaluates whether an LLM can do the job of an
air traffic controller. The model works a live simulated position: it receives radar
state and radio transcript as text, issues instructions in ICAO/FAA phraseology,
manages flight strips via tools, and is **scored deterministically** from the event
log — no LLM judges, ever.

See **[DESIGN.md](./DESIGN.md)** for the full specification and **[TASKS.md](./TASKS.md)**
for the work breakdown. Progress is tracked in the GitHub issues (one epic per phase).

## Status

Phases 1–2 (v1 = **CD + GND**) — deterministic **Clearance Delivery** and **Ground**
positions, turn-based regime, with full scoring, certification gating, and a
byte-identical replay determinism check.

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
- Half-duplex frequency channel (150 wpm broadcast physics) — `harness/session.py`
- Taxi graph + Ground position: taxi kinematics, explicit crossings, runway-incursion
  and head-on deadlock oracle — `sim/taxi.py`, `charts/kmrl_gnd.py`, `harness/ground_session.py`
- Model adapter + tool router: scripted oracle & bad controllers (CD and GND), a replay
  adapter, and an Anthropic adapter — `harness/adapters.py`
- Flight strip store + tools — `strips/store.py`
- CD + GND scorers, certification gates, feasibility oracle — `scoring/`, `baselines/`
- CLI: `run --position CD|GND` / `score` / `replay` — `cli.py`

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

## How scoring works (CD)

Each session yields `S = gate · (0.35·E + 0.25·H + 0.20·F + 0.20·A)`:

- **gate** — 0 if any *cardinal* violation (e.g., an aircraft departs with an
  uncorrected wrong altitude), else 0 if more than 2 *severe* events, else 1.
- **E** efficiency (service time), **H** hearback (caught / catchable readback errors),
  **F** frequency & protocol discipline, **A** attention.

Certification is absolute: 3/3 seeds with `gate = 1` at the standard band. The bundled
oracle controller certifies on every seed; the bad controller busts wherever the seed
schedules a safety-critical readback error.

## Determinism

The **environment** — pilots, physics, error injection, scoring — is a pure function
of `(seed, model_outputs)`. The model under test need not be deterministic; the replay
check records model outputs and asserts the event log is reproducible byte-for-byte.

## License

Apache-2.0.
