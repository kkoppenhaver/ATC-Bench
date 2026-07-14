"""Ground scorer (DESIGN §6.2, §13).

Pure function of the event log (+ scenario for the Tower sequence). Cardinal
violations are model-caused runway incursions (RI-CTRL) and head-on deadlocks
(DEADLOCK); pilot-caused incursions (from injected hold-short errors) are reported,
not fatal. Efficiency blends taxi delay vs. a shortest-path baseline with conformity
to the Tower-requested departure sequence.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..sim.events import EventLog

W_E, W_H, W_F, W_A = 0.35, 0.25, 0.20, 0.20
NEGLECT_THRESHOLD_SEC = 180  # spawn -> first taxi clearance beyond this = NEGLECT (§13.1)
STRANDED_THRESHOLD_SEC = 300  # taxi-cleared this long with no crossing and no arrival = NEGLECT

_ORACLE_CACHE: dict[tuple, dict] = {}


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _oracle_metrics(scenario: dict) -> dict:
    """Per-aircraft oracle taxi/latency for this seed — the §13.2 E/A normalizer.

    Replaces the old shortest-path-plus-fixed-allowance ideal: the oracle actually
    works the same traffic (same crossings, same hot windows), so its times are an
    achievable anchor rather than a slack absolute threshold."""
    key = (scenario["seed"], scenario["band"], scenario["session_seconds"])
    if key not in _ORACLE_CACHE:
        from ..harness.adapters import ScriptedGNDController
        from ..harness.ground_session import GroundSession
        from ..scenarios.gnd import generate

        olog = GroundSession(generate(key[0], band=key[1], session_seconds=key[2])).run(
            ScriptedGNDController()).log
        ospawns = {e.payload["acid"]: e.tick for e in olog.of_type("aircraft_spawn")}
        taxi: dict[str, int] = {}
        for e in olog.of_type("aircraft_arrived"):
            a = e.payload["acid"]
            if a in ospawns:
                taxi.setdefault(a, max(1, e.tick - ospawns[a]))
        latency: dict[str, int] = {}
        for e in olog.of_type("taxi_clearance"):
            a = e.payload["acid"]
            if a in ospawns:
                latency.setdefault(a, max(1, e.tick - ospawns[a]))
        _ORACLE_CACHE[key] = {"taxi": taxi, "latency": latency}
    return _ORACLE_CACHE[key]


def score_gnd(log: EventLog, scenario: dict) -> dict:
    spawns = {e.payload["acid"]: e for e in log.of_type("aircraft_spawn")}
    arrivals = {}
    for e in log.of_type("aircraft_arrived"):
        arrivals.setdefault(e.payload["acid"], e)
    first_taxi = {}
    for e in log.of_type("taxi_clearance"):
        first_taxi.setdefault(e.payload["acid"], e.tick)

    incursions = log.of_type("runway_incursion")
    deadlocks = log.of_type("deadlock")

    cardinals: list[dict] = []
    reported: list[dict] = []
    for e in incursions:
        if e.payload.get("provenance") == "model":
            cardinals.append({"code": "RI-CTRL", "acid": e.payload["acid"],
                              "runway": e.payload.get("runway"), "tick": e.tick})
        else:
            reported.append({"code": "pilot_incursion", "acid": e.payload["acid"], "tick": e.tick})
    for e in deadlocks:
        cardinals.append({"code": "DEADLOCK", "acids": e.payload.get("acids"), "tick": e.tick})

    session_end = max((e.tick for e in log.of_type("session_end")), default=0)

    # NEGLECT: a spawned aircraft whose first taxi clearance never came, or came past
    # the threshold — inaction is a cardinal, mirroring the CD neglect deadline (§13.1).
    for acid, sp_ev in spawns.items():
        first = first_taxi.get(acid)
        if first is None:
            if session_end - sp_ev.tick >= NEGLECT_THRESHOLD_SEC:
                cardinals.append({"code": "NEGLECT", "acid": acid,
                                  "detail": "no taxi clearance by session end",
                                  "tick": session_end})
        elif first - sp_ev.tick > NEGLECT_THRESHOLD_SEC:
            cardinals.append({"code": "NEGLECT", "acid": acid,
                              "detail": f"first taxi clearance after {first - sp_ev.tick} s",
                              "tick": first})

    # role/spawn/goal lookup from scenario
    spec = {}
    for s in scenario.get("departures", []) + scenario.get("arrivals", []):
        spec[s["acid"]] = s

    # Stranded: a departure taxi-cleared long ago that never got its runway crossing
    # and never reached the runway — abandonment at the hold bar is NEGLECT, distinct
    # from slow-but-ongoing service (which only costs E).
    crossing_cleared = {e.payload["acid"] for e in log.of_type("crossing_clearance")}
    for acid in spawns:
        if spec.get(acid, {}).get("role") != "departure":
            continue
        first = first_taxi.get(acid)
        if (first is not None and acid not in arrivals and acid not in crossing_cleared
                and session_end - first >= STRANDED_THRESHOLD_SEC):
            cardinals.append({"code": "NEGLECT", "acid": acid,
                              "detail": "stranded at hold short (no crossing clearance)",
                              "tick": session_end})

    # Every spawned aircraft counts in E and A; unserviced aircraft score 0 rather
    # than being silently excluded from the denominators. Ratios are oracle-normalized
    # per aircraft (§13.2), neutral if the oracle somehow lacks the aircraft.
    oracle = _oracle_metrics(scenario)
    delay_factors: list[float] = []
    response_ratios: list[float] = []
    for acid, sp_ev in spawns.items():
        if acid in arrivals:
            actual = max(1, arrivals[acid].tick - sp_ev.tick)
            delay_factors.append(_clamp(oracle["taxi"].get(acid, actual) / actual))
        else:
            delay_factors.append(0.0)
        if acid in first_taxi:
            lat = max(1, first_taxi[acid] - sp_ev.tick)
            response_ratios.append(_clamp(oracle["latency"].get(acid, lat) / lat))
        else:
            response_ratios.append(0.0)

    # Tower-sequence conformity: order departures reached the departure hold short.
    dep_order = [e.payload["acid"] for e in log.of_type("aircraft_arrived")
                 if spec.get(e.payload["acid"], {}).get("role") == "departure"]
    requested = [a for a in scenario.get("tower_sequence", []) if a in dep_order]
    queue_conf = _sequence_conformity(dep_order, requested)

    # Empty aggregates with aircraft present score 0, not 1: silence is not competence.
    taxi_eff = (sum(delay_factors) / len(delay_factors)) if delay_factors else (0.0 if spawns else 1.0)
    E = 0.7 * taxi_eff + 0.3 * queue_conf

    # Protocol (chart rules, audit M2): a taxi clearance whose route uses the crossing
    # taxiway must name the hold short explicitly. Violations are reported and don't
    # count as purposeful. Pure log check on the *transmitted* clearance.
    from ..charts import kmrl_gnd

    protocol_violations = 0
    for e in log.of_type("taxi_clearance"):
        via = [v.lower() for v in e.payload.get("via") or []]
        crosses = spec.get(e.payload["acid"], {}).get("role") == "departure" or "a" in via
        if crosses and kmrl_gnd.CROSSING_RUNWAY not in (e.payload.get("hold_short") or []):
            protocol_violations += 1
            reported.append({"code": "missing_hold_short", "acid": e.payload["acid"],
                             "tick": e.tick})

    # F: purposeful controller transmissions (taxi/crossing) over all controller tx.
    ctrl_tx = [e for e in log.of_type("transmission") if e.payload.get("speaker", "").endswith("_GND")]
    purposeful = (len(log.of_type("taxi_clearance")) - protocol_violations
                  + len(log.of_type("crossing_clearance")))
    F = _clamp(purposeful / len(ctrl_tx)) if ctrl_tx else (0.0 if spawns else 1.0)

    A = (sum(response_ratios) / len(response_ratios)) if response_ratios else (0.0 if spawns else 1.0)

    severe_count = 0
    gate = 0 if cardinals else (0 if severe_count > 2 else 1)
    # Hearback isn't exercised at the GND slice yet (no readback error classes), so H
    # is excluded and the remaining weights renormalized — no free credit (§13.2).
    s_raw = (W_E * E + W_F * F + W_A * A) / (W_E + W_F + W_A)
    S = gate * s_raw
    ttfc = min([c["tick"] for c in cardinals], default=None)

    return {
        "position": "MRL_GND",
        "gate": gate,
        "S": round(S, 4),
        "S_raw": round(s_raw, 4),
        "components": {"E": round(E, 4), "H": None, "F": round(F, 4), "A": round(A, 4)},
        "cardinal_violations": cardinals,
        "reported_events": reported,
        "counts": {
            "aircraft": len(spawns),
            "arrived": len(arrivals),
            "cardinals": len(cardinals),
            "incursions_model": sum(1 for c in cardinals if c["code"] == "RI-CTRL"),
            "deadlocks": sum(1 for c in cardinals if c["code"] == "DEADLOCK"),
            "neglects": sum(1 for c in cardinals if c["code"] == "NEGLECT"),
        },
        "queue_conformity": round(queue_conf, 4),
        "time_to_first_cardinal": ttfc,
    }


def _sequence_conformity(actual: list[str], requested: list[str]) -> float:
    """Fraction of requested adjacent pairs that appear in the correct relative order."""
    if len(requested) < 2:
        return 1.0
    pos = {a: i for i, a in enumerate(actual)}
    good = total = 0
    for a, b in zip(requested, requested[1:]):
        if a in pos and b in pos:
            total += 1
            good += 1 if pos[a] < pos[b] else 0
    return good / total if total else 1.0


def score_run_dir(run_dir: str | Path) -> dict:
    d = Path(run_dir)
    log = EventLog.read(d / "events.jsonl")
    scn = json.loads((d / "scenario.json").read_text(encoding="utf-8"))
    return score_gnd(log, scn)
