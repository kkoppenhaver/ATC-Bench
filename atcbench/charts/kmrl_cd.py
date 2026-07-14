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


@dataclass
class CDChartPack:
    """Per-scenario chart material (audit M6, P4.0d).

    The correct answers — departure frequency, SID set, per-SID initial altitudes,
    and the invalid-filing fallback rule — are drawn from the seeded ``airspace``
    stream per scenario, so a public repo (and its published run dirs) never carries
    a memorizable answer key. The pack is delivered in-context via the system prompt;
    scoring and the oracle read the same pack from the scenario record."""

    departure_frequency: str
    sids: dict[str, dict]
    fallback_sid: str  # LOA rule: assigned when a filed SID is invalid/unknown

    def sid_valid(self, sid: str) -> bool:
        return sid in self.sids

    def initial_altitude(self, sid: str) -> int:
        return self.sids[sid]["initial_altitude"]

    def destination_known(self, icao: str) -> bool:
        return icao in KNOWN_DESTINATIONS

    def to_dict(self) -> dict:
        return {"departure_frequency": self.departure_frequency,
                "sids": self.sids, "fallback_sid": self.fallback_sid}

    @classmethod
    def from_dict(cls, d: dict) -> "CDChartPack":
        return cls(departure_frequency=d["departure_frequency"],
                   sids=d["sids"], fallback_sid=d["fallback_sid"])

    def describe(self) -> str:
        """Human-readable chart-pack text for the system prompt (§11.3 section 2-4)."""
        lines = [
            f"FACILITY: {FACILITY} — {FACILITY_NAME} — {POSITION} ({CALLSIGN})",
            f"Departure control frequency: {self.departure_frequency}",
            "LOA (M90): initial altitude is PER SID — see the table below. Filed",
            "altitudes that conflict with the LOA are filing errors; the clearance",
            "altitude always comes from this table.",
            f"Invalid or unknown filed SIDs: assign the {self.fallback_sid} "
            f"({self.sids[self.fallback_sid]['name']}) departure.",
            "",
            "Published departures (SIDs):",
        ]
        for code, sid in self.sids.items():
            fixes = " ".join(sid.get("fixes", [])) or "(vectors)"
            lines.append(
                f"  - {code} ({sid['name']}, {sid['type']}): initial "
                f"{sid['initial_altitude']} ft; fixes: {fixes}"
            )
        lines.append("")
        lines.append("Served destinations (ICAO — name):")
        for icao, name in KNOWN_DESTINATIONS.items():
            lines.append(f"  - {icao}: {name}")
        return "\n".join(lines)


# Pronounceable fictional-name material for seeded SIDs/fixes (never real procedures).
_SYL_A = ["BAR", "CAL", "DOV", "FEN", "GRIM", "HOL", "KEL", "LAR", "MOR",
          "NED", "PEL", "QUIN", "RAV", "SOL", "TAV", "VEL", "WIN", "YAR"]
_SYL_B = ["BA", "DEE", "GO", "KA", "LIN", "MA", "NA", "PO", "RA", "SA", "TA", "VO"]
_NUM_WORDS = {2: "Two", 3: "Three", 4: "Four", 5: "Five",
              6: "Six", 7: "Seven", 8: "Eight", 9: "Nine"}


def _mk_sid(rng, used_bases: set[str]) -> tuple[str, str]:
    """Return (code, name) like ("KELRA4", "Kelra Four"); base unique within the pack."""
    while True:
        base = rng.choice(_SYL_A) + rng.choice(_SYL_B)
        if base not in used_bases:
            used_bases.add(base)
            break
    n = rng.randint(2, 9)
    return f"{base}{n}", f"{base.capitalize()} {_NUM_WORDS[n]}"


def _mk_fix(rng) -> str:
    return (rng.choice(_SYL_A) + rng.choice(_SYL_B))[:5].ljust(5, "A")


def generate_pack(rng) -> CDChartPack:
    """Draw a per-scenario CD chart pack from the seeded airspace stream."""
    freq = f"1{rng.randint(18, 27)}.{rng.randint(0, 19) * 5:02d}"
    used: set[str] = set()
    sids: dict[str, dict] = {}
    for _ in range(rng.randint(3, 5)):
        code, name = _mk_sid(rng, used)
        rnav = rng.random() < 0.6
        sid = {"name": name, "type": "rnav" if rnav else "vector",
               "initial_altitude": rng.choice([3000, 4000, 5000, 6000, 7000])}
        if rnav:
            sid["fixes"] = [_mk_fix(rng), _mk_fix(rng)]
        sids[code] = sid
    fallback = rng.choice(sorted(sids))
    return CDChartPack(departure_frequency=freq, sids=sids, fallback_sid=fallback)


def generate_invalid_sid(rng, pack: CDChartPack) -> str:
    """A plausible-looking SID code that is NOT in this pack (for filing errors)."""
    used = {c[:-1] for c in pack.sids}
    code, _ = _mk_sid(rng, used)
    return code


# Static legacy pack — kept for parser unit tests and any tooling that needs a fixed
# reference; real scenarios always carry their own seeded pack.
PACK = CDChartPack(departure_frequency=DEPARTURE_FREQUENCY, sids=SIDS,
                   fallback_sid="MRLW5")
