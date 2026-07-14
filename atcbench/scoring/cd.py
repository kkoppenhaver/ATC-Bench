"""Clearance Delivery scorer (DESIGN §13, §6.1).

A pure function of the event log (plus the scenario's expected clearances / error
schedule, which ship in the run directory). No LLM judges (principle #1). Weights are
v1-pinned (§13.2): S_raw = 0.35·E + 0.25·H + 0.20·F + 0.20·A, gated by safety.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..charts import kmrl_cd
from ..scenarios.cd import CATCHABLE_CLASSES
from ..sim.events import EventLog

# Pinned component weights (§13.2).
W_E, W_H, W_F, W_A = 0.35, 0.25, 0.20, 0.20
SERVICE_THRESHOLD_SEC = 90  # CD per-clearance service-time target (§6.1)
RESPONSE_THRESHOLD_SEC = 30  # target latency to first clearance after check-in


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def score_cd(log: EventLog, expected: dict[str, dict], error_schedule: dict[str, dict]) -> dict:
    spawns = {e.payload["acid"]: e.tick for e in log.of_type("aircraft_spawn")}
    cleared = {e.payload["acid"]: e for e in log.of_type("aircraft_cleared")}
    clearance_issued = {}
    for e in log.of_type("clearance_issued"):
        clearance_issued.setdefault(e.payload["acid"], e.tick)

    cardinals: list[dict] = []
    severes: list[dict] = []
    service_ratios: list[float] = []
    response_ratios: list[float] = []

    for acid, ev in cleared.items():
        p = ev.payload
        exp = expected.get(acid, {})
        comply = p.get("comply_as") or {}

        if p.get("neglected"):
            cardinals.append({"code": "NEGLECT", "acid": acid, "detail": "uncleared at timeout"})
        else:
            alt_ok = comply.get("altitude") == exp.get("altitude")
            dest_ok = comply.get("clearance_limit") == exp.get("clearance_limit")
            route = comply.get("route")
            route_ok = route is not None and kmrl_cd.PACK.sid_valid(route)
            squawk_ok = comply.get("squawk") == exp.get("squawk")
            freq_ok = comply.get("frequency") == exp.get("frequency")

            if not alt_ok:
                cardinals.append({"code": "NEGLECT", "acid": acid,
                                  "detail": f"departed at {comply.get('altitude')} not {exp.get('altitude')}"})
            elif not route_ok or not dest_ok:
                cardinals.append({"code": "NEGLECT", "acid": acid, "detail": "wrong route/destination"})
            if not squawk_ok:
                severes.append({"code": "wrong_squawk", "acid": acid})
            if not freq_ok:
                severes.append({"code": "wrong_frequency", "acid": acid})

        # Efficiency: service time from check-in to cleared.
        t0 = spawns.get(acid)
        if t0 is not None:
            service = max(1, ev.tick - t0)
            service_ratios.append(_clamp(SERVICE_THRESHOLD_SEC / service))
        # Attention proxy: latency from check-in to first clearance issued.
        if t0 is not None and acid in clearance_issued:
            latency = max(1, clearance_issued[acid] - t0)
            response_ratios.append(_clamp(RESPONSE_THRESHOLD_SEC / latency))

    # Hearback as signal detection: catch rate on scheduled errors minus false-alarm
    # rate on correct readbacks. Silence after a correct readback is a correct accept;
    # blanket "negative ..." spam catches everything but false-alarms everything and
    # scores 0 — listening, not re-clearing, is what H measures (§13.3).
    catchable = [a for a, e in error_schedule.items() if e.get("code") in CATCHABLE_CLASSES]
    caught = sum(1 for a in catchable if cleared.get(a) and cleared[a].payload.get("error_caught"))
    tpr = (caught / len(catchable)) if catchable else 1.0
    spurious_acids = {e.payload["acid"] for e in log.of_type("spurious_correction")}
    correct_rb = [a for a in cleared if a not in set(catchable)]
    fpr = (sum(1 for a in correct_rb if a in spurious_acids) / len(correct_rb)) if correct_rb else 0.0
    H = _clamp(tpr - fpr)

    # Frequency & protocol discipline: fraction of controller transmissions that
    # parsed to a purposeful, addressed intent (proxy for phraseology cleanliness).
    # Spurious corrections land in the denominator only — spam costs F.
    ctrl_tx = [e for e in log.of_type("transmission") if e.payload.get("speaker", "").endswith("_CD")]
    n_ctrl = len(ctrl_tx)
    purposeful = len(log.of_type("clearance_issued")) + len(log.of_type("clearance_corrected"))
    F = _clamp(purposeful / n_ctrl) if n_ctrl else 1.0

    E = (sum(service_ratios) / len(service_ratios)) if service_ratios else 1.0
    A = (sum(response_ratios) / len(response_ratios)) if response_ratios else 1.0

    severe_count = len(severes)
    gate = 0 if cardinals else (0 if severe_count > 2 else 1)
    s_raw = W_E * E + W_H * H + W_F * F + W_A * A
    S = gate * s_raw

    # time_to_first_cardinal (progress metric, §13.3).
    ttfc = None
    if cardinals:
        cardinal_ticks = [cleared[c["acid"]].tick for c in cardinals if c["acid"] in cleared]
        ttfc = min(cardinal_ticks) if cardinal_ticks else None

    return {
        "position": "MRL_CD",
        "gate": gate,
        "S": round(S, 4),
        "S_raw": round(s_raw, 4),
        "components": {"E": round(E, 4), "H": round(H, 4), "F": round(F, 4), "A": round(A, 4)},
        "cardinal_violations": cardinals,
        "severe_events": severes,
        "counts": {
            "aircraft": len(cleared),
            "catchable_errors": len(catchable),
            "caught_errors": caught,
            "spurious_corrections": len(log.of_type("spurious_correction")),
            "cardinals": len(cardinals),
            "severes": severe_count,
        },
        "time_to_first_cardinal": ttfc,
    }


def score_run_dir(run_dir: str | Path) -> dict:
    """Re-score a run directory from its log alone (no model calls; §17.1)."""
    d = Path(run_dir)
    log = EventLog.read(d / "events.jsonl")
    scn: dict[str, Any] = json.loads((d / "scenario.json").read_text(encoding="utf-8"))
    return score_cd(log, scn.get("expected_clearance", {}), scn.get("error_schedule", {}))
