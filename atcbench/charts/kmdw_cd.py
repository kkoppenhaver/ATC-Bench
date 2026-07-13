"""KMDW Clearance Delivery chart pack (DESIGN §5.1, §6.1, §11.3).

A deliberately small, benchmark-normalized slice of Chicago Midway's clearance
data — enough to issue and check IFR clearances in CRAFT order and to catch filing
errors (invalid route segment, wrong initial altitude per LOA). Everything here is
supplied to the model in its system prompt; nothing is hidden (principle #6).

This is hand-built reference data, not a claim of operational accuracy — the SIDs,
frequencies, and altitudes are simplified for the benchmark.
"""

from __future__ import annotations

from dataclasses import dataclass

FACILITY = "KMDW"
POSITION = "MDW_CD"
CALLSIGN = "Midway Clearance"

# Departure control frequency handed off to in the clearance (F of CRAFT).
DEPARTURE_FREQUENCY = "119.35"

# Published RNAV/vector SIDs at KMDW (simplified to name → transition set).
# A filed route is valid iff its SID is one of these and its first fix is served.
SIDS: dict[str, dict] = {
    "MDW7": {  # "Midway Seven" departure (vector SID)
        "name": "Midway Seven",
        "type": "vector",
        "initial_altitude": 5000,  # feet, per LOA with C90
    },
    "PANGG5": {
        "name": "Pangg Five",
        "type": "rnav",
        "initial_altitude": 5000,
        "fixes": ["PANGG", "GIJ"],
    },
    "HALIE4": {
        "name": "Halie Four",
        "type": "rnav",
        "initial_altitude": 5000,
        "fixes": ["HALIE", "EON"],
    },
}

# Initial altitude the LOA with Chicago TRACON (C90) requires for MDW departures.
LOA_INITIAL_ALTITUDE = 5000

# Destinations reachable via the modeled SIDs (for route-validity checks).
KNOWN_DESTINATIONS = {
    "KDTW": "Detroit",
    "KLGA": "LaGuardia",
    "KMCO": "Orlando",
    "KATL": "Atlanta",
    "KDCA": "Washington National",
    "KBOS": "Boston",
}


@dataclass(frozen=True)
class ClearancePack:
    facility: str = FACILITY
    position: str = POSITION
    callsign: str = CALLSIGN
    departure_frequency: str = DEPARTURE_FREQUENCY
    loa_initial_altitude: int = LOA_INITIAL_ALTITUDE

    def sid_valid(self, sid: str) -> bool:
        return sid in SIDS

    def destination_known(self, icao: str) -> bool:
        return icao in KNOWN_DESTINATIONS


PACK = ClearancePack()


def describe() -> str:
    """Human-readable chart-pack text for the system prompt (§11.3 section 2-4)."""
    lines = [
        f"FACILITY: {FACILITY} — {POSITION} ({CALLSIGN})",
        f"Departure control frequency: {DEPARTURE_FREQUENCY}",
        f"LOA (C90): initial altitude for all MDW IFR departures = "
        f"{LOA_INITIAL_ALTITUDE} ft.",
        "",
        "Published departures (SIDs):",
    ]
    for code, sid in SIDS.items():
        fixes = " ".join(sid.get("fixes", [])) or "(vectors)"
        lines.append(
            f"  - {code} ({sid['name']}, {sid['type']}): initial {sid['initial_altitude']} ft; "
            f"fixes: {fixes}"
        )
    lines.append("")
    lines.append("Served destinations (ICAO — name):")
    for icao, name in KNOWN_DESTINATIONS.items():
        lines.append(f"  - {icao}: {name}")
    return "\n".join(lines)
