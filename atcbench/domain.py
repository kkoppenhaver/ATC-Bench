"""Shared domain dataclasses used across the CD slice.

Kept provider-agnostic and JSON-friendly so they serialize cleanly into
``scenario.json`` and the event log.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Optional


class Persona(str, Enum):
    """Pilot personas (DESIGN §8.2). Drive FSM parameters and verbalizer tone."""

    AIRLINE_CRISP = "airline_crisp"
    GA_RELAXED = "ga_relaxed"
    STUDENT_PILOT = "student_pilot"
    FOREIGN_CARRIER = "foreign_carrier"
    UNFAMILIAR = "unfamiliar"


@dataclass
class Clearance:
    """The CRAFT elements of an IFR clearance (DESIGN §6.1).

    ``altitude`` is the initial altitude (feet), ``frequency`` the departure
    frequency string, ``squawk`` the 4-digit transponder code.
    """

    clearance_limit: str  # destination ICAO
    route: str  # SID code (+ "as filed"); simplified to the SID for the CD slice
    altitude: int
    frequency: str
    squawk: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class FlightPlan:
    """A filed IFR flight plan. May contain a seeded filing error (§6.1)."""

    acid: str  # aircraft callsign, e.g. "AAL2452"
    actype: str  # e.g. "B738"
    destination: str  # ICAO
    filed_sid: str  # SID code as filed
    filed_altitude: int  # cruise/requested altitude filed by pilot
    persona: Persona = Persona.AIRLINE_CRISP
    # If set, the plan as filed contains this error the controller should catch
    # and correct before issuing clearance. None => clean plan.
    filing_error: Optional[str] = None  # e.g. "invalid_sid", "wrong_initial_altitude"
    call_tick: int = 0  # sim-second at which the pilot calls "ready for clearance"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["persona"] = self.persona.value
        return d


@dataclass
class ErrorEvent:
    """One scheduled readback/behavior error for an aircraft (DESIGN §8.3)."""

    code: str  # e.g. "RB-ALT", "RB-FREQ", "RB-DROP", "CS-CONF"
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"code": self.code, "detail": self.detail}
