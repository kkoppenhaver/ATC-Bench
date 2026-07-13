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

from ..charts import kmdw_gnd
from ..sim.events import EventLog

W_E, W_H, W_F, W_A = 0.35, 0.25, 0.20, 0.20
SWEEP_SEC = 5
RESPONSE_THRESHOLD_SEC = 30
CROSSING_ALLOWANCE_SEC = 150  # slack granted to routes that must wait for a runway crossing


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _ideal_seconds(spawn_node: str, goal_node: str, crosses: bool) -> float:
    path = kmdw_gnd.GRAPH.shortest_path(spawn_node, goal_node)
    sweeps = 0
    for u, v in zip(path, path[1:]):
        edge = kmdw_gnd.GRAPH.edge(u, v)
        if edge:
            sweeps += edge.transit_sweeps()
    base = sweeps * SWEEP_SEC
    return base + (CROSSING_ALLOWANCE_SEC if crosses else 0)


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

    # role/spawn/goal lookup from scenario
    spec = {}
    for s in scenario.get("departures", []) + scenario.get("arrivals", []):
        spec[s["acid"]] = s

    delay_factors: list[float] = []
    response_ratios: list[float] = []
    for acid, sp_ev in spawns.items():
        s = spec.get(acid, {})
        crosses = s.get("role") == "departure"
        if acid in arrivals:
            actual = max(1, arrivals[acid].tick - sp_ev.tick)
            ideal = _ideal_seconds(s.get("node", ""), s.get("goal", ""), crosses)
            delay_factors.append(_clamp(ideal / actual))
        if acid in first_taxi:
            response_ratios.append(_clamp(RESPONSE_THRESHOLD_SEC / max(1, first_taxi[acid] - sp_ev.tick)))

    # Tower-sequence conformity: order departures reached the departure hold short.
    dep_order = [e.payload["acid"] for e in log.of_type("aircraft_arrived")
                 if spec.get(e.payload["acid"], {}).get("role") == "departure"]
    requested = [a for a in scenario.get("tower_sequence", []) if a in dep_order]
    queue_conf = _sequence_conformity(dep_order, requested)

    taxi_eff = (sum(delay_factors) / len(delay_factors)) if delay_factors else 1.0
    E = 0.7 * taxi_eff + 0.3 * queue_conf

    # F: purposeful controller transmissions (taxi/crossing) over all controller tx.
    ctrl_tx = [e for e in log.of_type("transmission") if e.payload.get("speaker", "").endswith("_GND")]
    purposeful = len(log.of_type("taxi_clearance")) + len(log.of_type("crossing_clearance"))
    F = _clamp(purposeful / len(ctrl_tx)) if ctrl_tx else 1.0

    H = 1.0  # hearback not exercised at the GND slice (no readback error classes yet)
    A = (sum(response_ratios) / len(response_ratios)) if response_ratios else 1.0

    severe_count = 0
    gate = 0 if cardinals else (0 if severe_count > 2 else 1)
    s_raw = W_E * E + W_H * H + W_F * F + W_A * A
    S = gate * s_raw
    ttfc = min([c["tick"] for c in cardinals], default=None)

    return {
        "position": "MDW_GND",
        "gate": gate,
        "S": round(S, 4),
        "S_raw": round(s_raw, 4),
        "components": {"E": round(E, 4), "H": round(H, 4), "F": round(F, 4), "A": round(A, 4)},
        "cardinal_violations": cardinals,
        "reported_events": reported,
        "counts": {
            "aircraft": len(spawns),
            "arrived": len(arrivals),
            "cardinals": len(cardinals),
            "incursions_model": sum(1 for c in cardinals if c["code"] == "RI-CTRL"),
            "deadlocks": sum(1 for c in cardinals if c["code"] == "DEADLOCK"),
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
