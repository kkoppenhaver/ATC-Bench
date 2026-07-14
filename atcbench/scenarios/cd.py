"""Clearance Delivery scenario generator (DESIGN §6.1, §8.3, §8.4, §12).

Produces a deterministic queue of filed flight plans (some flawed), pilot call
times, personas, similar-callsign pairs, and a per-aircraft readback error schedule.
Every model evaluated on a seed faces identical plans and identical errors at
identical sim-seconds (principle #2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ..charts import kmrl_cd
from ..domain import ErrorEvent, FlightPlan, Persona
from . import fleet
from .seeds import SeedManager

# Difficulty bands (§12.3). Certification uses "standard".
BANDS = {
    "calm": {"n_aircraft": 6, "error_rate": 0.15, "filing_error_rate": 0.15, "similar_pairs": 0},
    "standard": {"n_aircraft": 10, "error_rate": 0.35, "filing_error_rate": 0.25, "similar_pairs": 1},
    "heavy": {"n_aircraft": 16, "error_rate": 0.55, "filing_error_rate": 0.35, "similar_pairs": 2},
}

# Readback error classes catchable from the pilot's readback (feed the Hearback
# metric H, §13.2). RB-DROP is a missing readback — caught by noticing silence.
CATCHABLE_CLASSES = {"RB-ALT", "RB-FREQ", "RB-PART", "RB-DROP", "CS-WRONG"}
SPECIAL_SQUAWKS = {"7500", "7600", "7700"}  # hijack/radio-failure/emergency — never assigned

_AIRLINE_PERSONAS = [
    Persona.AIRLINE_CRISP, Persona.AIRLINE_CRISP, Persona.AIRLINE_CRISP,
    Persona.AIRLINE_CRISP, Persona.FOREIGN_CARRIER,
]
_GA_PERSONAS = [Persona.GA_RELAXED, Persona.GA_RELAXED, Persona.STUDENT_PILOT]
_AIRLINES = ["AAL", "UAL", "SWA", "DAL"]
_DESTINATIONS = list(kmrl_cd.KNOWN_DESTINATIONS)


@dataclass
class Scenario:
    seed: int
    band: str
    position: str
    session_seconds: int
    flight_plans: list[FlightPlan]
    # Seed-drawn chart material (audit M6): the answers live here, not in the repo.
    chart_pack: kmrl_cd.CDChartPack = field(default_factory=lambda: kmrl_cd.PACK)
    # Per-aircraft readback error schedule; acid -> ErrorEvent (or absent = clean).
    error_schedule: dict[str, ErrorEvent] = field(default_factory=dict)
    # acid -> the correct clearance the controller *should* issue after any fixes.
    expected_clearance: dict[str, dict] = field(default_factory=dict)
    similar_pairs: list[tuple[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "seed": self.seed,
            "band": self.band,
            "position": self.position,
            "session_seconds": self.session_seconds,
            "chart_pack": self.chart_pack.to_dict(),
            "flight_plans": [fp.to_dict() for fp in self.flight_plans],
            "error_schedule": {k: v.to_dict() for k, v in self.error_schedule.items()},
            "expected_clearance": self.expected_clearance,
            "similar_pairs": [list(p) for p in self.similar_pairs],
        }

    def plan(self, acid: str) -> Optional[FlightPlan]:
        return next((fp for fp in self.flight_plans if fp.acid == acid), None)




def _correct_clearance(fp: FlightPlan, squawk: str, pack: kmrl_cd.CDChartPack) -> dict:
    """The clearance the controller should issue for a plan, after fixing filings.

    A filing error changes the correct answer (audit M6): an invalid SID reroutes to
    the pack's fallback SID, and the altitude follows the assigned SID's LOA entry —
    so catching a bad filing requires the chart lookup, not a memorized constant."""
    route = fp.filed_sid if pack.sid_valid(fp.filed_sid) else pack.fallback_sid
    return {
        "clearance_limit": fp.destination,
        "route": route,
        "altitude": pack.initial_altitude(route),
        "frequency": pack.departure_frequency,
        "squawk": squawk,
    }


def _transpose_freq(freq: str) -> str:
    digits = freq.replace(".", "")
    d = list(digits)
    d[-1], d[-2] = d[-2], d[-1]
    if "".join(d) == digits:  # last two digits equal — transpose the MHz digits instead
        d = list(digits)
        d[1], d[2] = d[2], d[1]
    digits = "".join(d)
    return f"{digits[:3]}.{digits[3:]}"


def generate(seed: int, band: str = "standard", session_seconds: int = 3600) -> Scenario:
    if band not in BANDS:
        raise ValueError(f"unknown band {band!r}; choose from {list(BANDS)}")
    cfg = BANDS[band]
    sm = SeedManager(seed)
    traffic = sm.stream("traffic")
    errors = sm.stream("errors")
    callsigns = sm.stream("callsigns")
    airspace = sm.stream("airspace")

    # The chart pack — frequency, SID set, per-SID LOA altitudes, fallback rule —
    # is drawn per scenario (audit M6): no seed-independent answer key.
    pack = kmrl_cd.generate_pack(airspace)

    n = cfg["n_aircraft"]
    used: set[str] = set()
    plans: list[FlightPlan] = []

    # Spread call times uniformly across the session. (A banked/"lumpy" mid-session
    # cluster is future difficulty work — this draw is uniform.)
    call_ticks = sorted(traffic.randint(30, session_seconds - 120) for _ in range(n))

    for i in range(n):
        # Airframe first, then a type-appropriate callsign and persona (P4.0e):
        # GA types fly N-number registrations with GA voices, never airline flags.
        actype = traffic.choice(["B738", "A320", "B739", "E175", "C172"])
        acid = fleet.make_callsign(callsigns, actype, _AIRLINES, used)
        persona = traffic.choice(_GA_PERSONAS if actype in fleet.GA_TYPES
                                 else _AIRLINE_PERSONAS)
        dest = traffic.choice(_DESTINATIONS)
        filed_sid = traffic.choice(sorted(pack.sids))
        filed_alt = traffic.choice([16000, 17000, 23000, 35000])

        filing_error: Optional[str] = None
        if traffic.random() < cfg["filing_error_rate"]:
            kind = traffic.choice(["invalid_sid", "wrong_initial_altitude"])
            if kind == "invalid_sid":
                # A seeded plausible-but-unpublished SID: reroutes to the pack's
                # fallback, which also changes the correct LOA altitude (audit M6).
                filed_sid = kmrl_cd.generate_invalid_sid(traffic, pack)
                filing_error = "invalid_sid"
            else:
                # Filed an initial altitude that violates this pack's LOA table.
                filed_alt = pack.initial_altitude(filed_sid) + traffic.choice([2000, 3000, 4000])
                filing_error = "wrong_initial_altitude"

        plans.append(
            FlightPlan(
                acid=acid,
                actype=actype,
                destination=dest,
                filed_sid=filed_sid,
                filed_altitude=filed_alt,
                persona=persona,
                filing_error=filing_error,
                call_tick=call_ticks[i],
            )
        )

    # Similar-callsign pairs (§8.4): mint edit-distance-1 numeric twins, same airline.
    # Twins are an airline phenomenon — never minted from N-number registrations.
    similar_pairs: list[tuple[str, str]] = []
    airline_plans = [p for p in plans if not p.acid.startswith("N")]
    for _ in range(cfg["similar_pairs"]):
        if not airline_plans:
            break
        base = callsigns.choice(airline_plans)
        import re

        m = re.match(r"([A-Za-z]+)(\d+)", base.acid)
        if not m:
            continue
        prefix, num = m.group(1), int(m.group(2))
        twin_num = num + callsigns.choice([-1, 1, 10, -10])
        twin_acid = f"{prefix}{abs(twin_num)}"
        if twin_acid in used:
            continue
        used.add(twin_acid)
        twin = FlightPlan(
            acid=twin_acid,
            actype="B738",
            destination=callsigns.choice(_DESTINATIONS),
            filed_sid=callsigns.choice(sorted(pack.sids)),
            filed_altitude=16000,
            persona=Persona.AIRLINE_CRISP,
            call_tick=min(session_seconds - 60, base.call_tick + callsigns.randint(20, 90)),
        )
        plans.append(twin)
        similar_pairs.append((base.acid, twin_acid))

    plans.sort(key=lambda p: p.call_tick)

    # Assign squawks and build the expected (correct) clearances + error schedule.
    expected: dict[str, dict] = {}
    schedule: dict[str, ErrorEvent] = {}
    for fp in plans:
        while True:
            squawk = f"{errors.randint(1, 7)}{errors.randint(0, 7)}{errors.randint(0, 7)}{errors.randint(0, 7)}"
            if squawk not in SPECIAL_SQUAWKS:
                break
        expected[fp.acid] = _correct_clearance(fp, squawk, pack)

        if errors.random() < cfg["error_rate"]:
            code = errors.choice(["RB-ALT", "RB-FREQ", "RB-PART", "RB-DROP"])
            detail: dict = {}
            if code == "RB-ALT":
                # A near-miss relative to this aircraft's correct altitude — the
                # audible error content is seeded, not a repo constant (audit M6).
                correct_alt = expected[fp.acid]["altitude"]
                detail["wrong_altitude"] = correct_alt + errors.choice(
                    [-2000, -1000, 1000, 2000, 10000])
            elif code == "RB-FREQ":
                detail["wrong_frequency"] = _transpose_freq(pack.departure_frequency)
            elif code == "RB-PART":
                detail["omit"] = errors.choice(["squawk", "frequency"])
            schedule[fp.acid] = ErrorEvent(code=code, detail=detail)

    # Pair a CS-WRONG (wrong callsign in readback) with each similar pair.
    for a, b in similar_pairs:
        schedule[a] = ErrorEvent(code="CS-WRONG", detail={"wrong_callsign": b})

    return Scenario(
        seed=seed,
        band=band,
        position="MRL_CD",
        session_seconds=session_seconds,
        flight_plans=plans,
        chart_pack=pack,
        error_schedule=schedule,
        expected_clearance=expected,
        similar_pairs=similar_pairs,
    )
