"""Marlow Regional (KMRL) — Clearance Delivery chart pack (DESIGN §5.1, §6.1, §11.3).

⚠️ FICTIONAL FACILITY. Marlow Regional Airport (KMRL) does not exist. Its runways,
SIDs, fixes, frequencies, LOA, and gate layout are fabricated for the benchmark — a
placeholder so the harness, scoring, and determinism machinery can be built and
tested before real FAA chart packs are digitized. It is a Generalist-style pack
wearing a facility nameplate; it does NOT yet exercise the "real airport the model
may know from training" premise of the Facility track. Real facility packs (parsed
from public FAA airport diagrams and procedure plates) are future work — see
DESIGN §5.1 and task P1.10/P2.2. The fabrication is flagged programmatically via
``FACILITY_KIND``.
"""

from __future__ import annotations

from dataclasses import dataclass

FACILITY = "KMRL"  # fictional
FACILITY_NAME = "Marlow Regional Airport"
FACILITY_KIND = "fictional"  # {"fictional", "real"} — logged into run records
POSITION = "MRL_CD"
CALLSIGN = "Marlow Clearance"

# Departure control frequency handed off to in the clearance (F of CRAFT). Fabricated.
DEPARTURE_FREQUENCY = "119.35"

# Fabricated SIDs (name → transition set). A filed route is valid iff its SID is here.
SIDS: dict[str, dict] = {
    "MRLW5": {  # "Marlow Five" departure (vector SID)
        "name": "Marlow Five",
        "type": "vector",
        "initial_altitude": 5000,  # feet, per (fabricated) LOA with Marlow TRACON
    },
    "PANGG5": {
        "name": "Pangg Five",
        "type": "rnav",
        "initial_altitude": 5000,
        "fixes": ["PANGG", "GRIST"],
    },
    "HALIE4": {
        "name": "Halie Four",
        "type": "rnav",
        "initial_altitude": 5000,
        "fixes": ["HALIE", "EONNA"],
    },
}

# Fabricated LOA with Marlow TRACON (M90): initial altitude for all departures.
LOA_INITIAL_ALTITUDE = 5000

# Destinations are real cities (flights from a fictional field can go real places).
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
    facility_kind: str = FACILITY_KIND
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
        f"FACILITY: {FACILITY} — {FACILITY_NAME} — {POSITION} ({CALLSIGN})",
        f"Departure control frequency: {DEPARTURE_FREQUENCY}",
        f"LOA (M90): initial altitude for all {FACILITY} IFR departures = "
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
