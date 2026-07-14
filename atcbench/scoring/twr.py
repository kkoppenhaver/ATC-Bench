"""Tower scorer (DESIGN §6.3, §13).

Pure function of the event log. Cardinal violations are model-caused simultaneous
runway occupancy (LoS) and wake-separation busts. Efficiency blends completion
(throughput vs. the scenario's traffic) with a penalty for *model-caused* go-arounds;
environment-forced go-arounds (blown tire) are excluded via event provenance.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..sim.events import EventLog

W_E, W_H, W_F, W_A = 0.35, 0.25, 0.20, 0.20
NEGLECT_GRACE_SEC = 180  # min time on frequency before a never-cleared aircraft is NEGLECT

_ORACLE_CACHE: dict[tuple, dict] = {}


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _oracle_metrics(scenario: dict) -> dict:
    """Oracle completion / go-arounds / per-aircraft clearance latency — the §13.2
    normalizer. Anchors E and A to what the conservative serialized oracle achieves on
    exactly this traffic (the old 60 s response threshold was unreachable for arrivals
    correctly cleared at 4 nm, silently capping A around 0.4)."""
    key = (scenario["seed"], scenario["band"], scenario["session_seconds"])
    if key not in _ORACLE_CACHE:
        from ..harness.adapters import ScriptedTWRController
        from ..harness.tower_session import TowerSession
        from ..scenarios.twr import generate

        olog = TowerSession(generate(key[0], band=key[1], session_seconds=key[2])).run(
            ScriptedTWRController()).log
        ospawns = {e.payload["acid"]: e.tick for e in olog.of_type("aircraft_spawn")}
        latency: dict[str, int] = {}
        for e in (olog.of_type("landing_clearance") + olog.of_type("takeoff_clearance")
                  + olog.of_type("luaw_clearance")):
            a = e.payload["acid"]
            if a in ospawns:
                latency.setdefault(a, max(1, e.tick - ospawns[a]))
        completed = len(olog.of_type("landed")) + len(olog.of_type("departed_sector"))
        model_ga = sum(1 for e in olog.of_type("go_around")
                       if e.payload.get("provenance") == "model")
        _ORACLE_CACHE[key] = {
            "latency": latency,
            "completion": completed / max(1, len(ospawns)),
            "model_ga": model_ga,
        }
    return _ORACLE_CACHE[key]


def score_twr(log: EventLog, scenario: dict) -> dict:
    n_arr = len(scenario.get("arrivals", []))
    n_dep = len(scenario.get("departures", []))
    total = max(1, n_arr + n_dep)

    spawns = {e.payload["acid"]: e.tick for e in log.of_type("aircraft_spawn")}
    landed = {e.payload["acid"] for e in log.of_type("landed")}
    departed = {e.payload["acid"] for e in log.of_type("departed_sector")}
    completed = len(landed) + len(departed)

    cardinals: list[dict] = []
    for e in log.of_type("los_event"):
        cardinals.append({"code": "LOS", "acids": e.payload.get("acids"),
                          "kind": e.payload.get("kind"), "tick": e.tick})
    for e in log.of_type("wake_violation"):
        cardinals.append({"code": "WAKE", "acid": e.payload.get("acid"),
                          "gap": e.payload.get("gap"), "required": e.payload.get("required"),
                          "tick": e.tick})

    go_arounds = log.of_type("go_around")
    model_ga = [e for e in go_arounds if e.payload.get("provenance") == "model"]
    env_ga = [e for e in go_arounds if e.payload.get("provenance") == "environment"]

    session_end = max((e.tick for e in log.of_type("session_end")), default=0)

    # First clearance latency per aircraft (attention proxy). LUAW counts as service.
    first_clear = {}
    for e in (log.of_type("landing_clearance") + log.of_type("takeoff_clearance")
              + log.of_type("luaw_clearance")):
        first_clear.setdefault(e.payload["acid"], e.tick)

    # NEGLECT: an aircraft on frequency past the grace period that never received any
    # clearance — inaction is a cardinal (§13.1). A latency threshold would false-flag
    # legitimate gap management, so the test is "never cleared at all".
    for acid, t0 in spawns.items():
        if acid not in first_clear and session_end - t0 >= NEGLECT_GRACE_SEC:
            cardinals.append({"code": "NEGLECT", "acid": acid,
                              "detail": "no clearance issued all session", "tick": session_end})

    # Every spawned aircraft counts in A; never-cleared aircraft score 0 rather than
    # being silently excluded from the denominator. Latencies are oracle-normalized
    # per aircraft (§13.2), neutral if the oracle somehow lacks the aircraft.
    oracle = _oracle_metrics(scenario)
    response_ratios = []
    for acid, t0 in spawns.items():
        if acid in first_clear:
            lat = max(1, first_clear[acid] - t0)
            response_ratios.append(_clamp(oracle["latency"].get(acid, lat) / lat))
        else:
            response_ratios.append(0.0)

    # E: throughput vs the oracle on this traffic, and go-arounds in excess of the
    # oracle's (a commanded go-around can be the right call — only extras count).
    completion = completed / total
    excess_ga = max(0, len(model_ga) - oracle["model_ga"])
    excess_ga_rate = _clamp(excess_ga / n_arr) if n_arr else 0.0
    E = 0.7 * _clamp(completion / oracle["completion"] if oracle["completion"] else completion) \
        + 0.3 * (1.0 - excess_ga_rate)

    # F: only events that are 1:1 with a model transmission count as purposeful —
    # a go-around forced by inaction or the environment is not a transmission.
    ctrl_tx = [e for e in log.of_type("transmission") if e.payload.get("speaker", "").endswith("_TWR")]
    commanded_ga = [e for e in go_arounds if e.payload.get("commanded")]
    purposeful = (len(log.of_type("landing_clearance")) + len(log.of_type("takeoff_clearance"))
                  + len(log.of_type("luaw_clearance")) + len(log.of_type("departed_sector"))
                  + len(commanded_ga))
    F = _clamp(purposeful / len(ctrl_tx)) if ctrl_tx else (0.0 if spawns else 1.0)

    A = (sum(response_ratios) / len(response_ratios)) if response_ratios else (0.0 if spawns else 1.0)

    gate = 0 if cardinals else 1
    # Hearback isn't exercised at the TWR slice yet (no readback error classes), so H
    # is excluded and the remaining weights renormalized — no free credit (§13.2).
    s_raw = (W_E * E + W_F * F + W_A * A) / (W_E + W_F + W_A)
    S = gate * s_raw
    ttfc = min([c["tick"] for c in cardinals], default=None)

    return {
        "position": "MRL_TWR",
        "gate": gate,
        "S": round(S, 4),
        "S_raw": round(s_raw, 4),
        "components": {"E": round(E, 4), "H": None, "F": round(F, 4), "A": round(A, 4)},
        "cardinal_violations": cardinals,
        "counts": {
            "aircraft": total,
            "completed": completed,
            "cardinals": len(cardinals),
            "los": sum(1 for c in cardinals if c["code"] == "LOS"),
            "wake": sum(1 for c in cardinals if c["code"] == "WAKE"),
            "neglects": sum(1 for c in cardinals if c["code"] == "NEGLECT"),
            "model_go_arounds": len(model_ga),
            "env_go_arounds": len(env_ga),
        },
        "time_to_first_cardinal": ttfc,
    }


def score_run_dir(run_dir: str | Path) -> dict:
    d = Path(run_dir)
    log = EventLog.read(d / "events.jsonl")
    scn = json.loads((d / "scenario.json").read_text(encoding="utf-8"))
    return score_twr(log, scn)
