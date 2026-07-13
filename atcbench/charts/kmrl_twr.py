"""Marlow Regional (KMRL) — Tower / Local chart pack (DESIGN §5.1, §6.3).

⚠️ FICTIONAL FACILITY (see kmrl_cd.py). Single active runway 31C with a simplified
pattern: arrivals appear on an ~8 NM final (handed off from Approach), departures are
delivered to the hold short by Ground. The Local controller sequences one runway —
takeoff/landing clearances with runway-occupancy and wake separation, gap management,
go-arounds, and line-up-and-wait — then hands departures to Approach.
"""

from __future__ import annotations

FACILITY = "KMRL"
FACILITY_NAME = "Marlow Regional Airport"
FACILITY_KIND = "fictional"
POSITION = "MRL_TWR"
CALLSIGN = "Marlow Tower"

RUNWAY = "31C"
FINAL_START_NM = 8.0            # arrivals appear here on final
GO_AROUND_NM = 1.0             # no landing clearance by here -> go around
OCCUPY_LAND_SEC = 45           # runway occupied from threshold crossing until vacated
OCCUPY_DEP_SEC = 35            # runway occupied from takeoff roll until airborne + past
GO_AROUND_REENTRY_SEC = 240    # a go-around re-enters the ~8 NM final after this delay
DEPARTURE_FREQUENCY = "119.35"  # Approach, for the departure handoff


def describe() -> str:
    return "\n".join([
        f"FACILITY: {FACILITY} — {FACILITY_NAME} — {POSITION} ({CALLSIGN})",
        f"Active runway: {RUNWAY} (single runway).",
        "",
        "TRAFFIC:",
        f"  - Arrivals check in on an ~{FINAL_START_NM:.0f} NM final (handed off from Approach).",
        "  - Departures are delivered to the hold short by Ground.",
        "",
        "RULES (§6.3, §4.5):",
        "  - Only ONE aircraft may occupy the runway at a time (landing rollout, takeoff",
        "    roll, or line-up-and-wait all count as occupying).",
        "  - Honor wake separation between successive runway uses (heavier leader = more).",
        f"  - Issue a landing clearance before the arrival reaches {GO_AROUND_NM:.0f} NM, or it",
        "    goes around; never clear a landing/takeoff onto an occupied runway.",
        "  - 'Line up and wait' puts a departure on the runway; clear its takeoff and let it",
        "    get airborne before an arrival crosses the threshold.",
        f"  - Hand airborne departures to Approach on {DEPARTURE_FREQUENCY}.",
    ])
