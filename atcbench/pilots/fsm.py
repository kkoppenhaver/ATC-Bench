"""Per-aircraft pilot FSM — Clearance Delivery subset (DESIGN §8.1, §8.2).

The FSM decides *what the pilot does*: check in, read back (correctly or with a
scheduled misspeak), accept a correction. It is a pure function of
``(state, parsed_instruction, error_schedule, tick)``. The verbalizer turns its
intent into words; the aircraft "flies" the accepted readback, not what was
transmitted (principle #4) — so an uncaught RB-ALT departs with the wrong altitude.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from ..domain import ErrorEvent, FlightPlan


class CDState(str, Enum):
    OFF_FREQ = "OFF_FREQ"
    AWAITING_CLEARANCE = "AWAITING_CLEARANCE"
    READBACK_PENDING = "READBACK_PENDING"
    CLEARED = "CLEARED"


# Clearance elements the pilot reads back (safety-critical numerics + route).
_ELEMENTS = ("clearance_limit", "route", "altitude", "frequency", "squawk")


class PilotFSM:
    def __init__(self, plan: FlightPlan, error: Optional[ErrorEvent], correction_window_sec: int = 30):
        self.plan = plan
        self.acid = plan.acid
        self.persona = plan.persona
        self.error = error
        self.correction_window_sec = correction_window_sec
        self.state = CDState.OFF_FREQ

        self.understood: dict = {}  # what the pilot heard (correct for catchable classes)
        self.readback: dict = {}  # what the pilot said (may carry a misspeak)
        self.comply_as: dict = {}  # what the aircraft will actually do
        self.readback_acid: str = self.acid  # callsign used in the readback (CS-WRONG twists this)
        self.error_caught: bool = False
        self.readback_dropped: bool = False
        self.readback_deadline: Optional[int] = None

    # --- transitions ---------------------------------------------------------

    def check_in(self, tick: int) -> dict:
        self.state = CDState.AWAITING_CLEARANCE
        from ..charts import kmdw_cd

        dest_name = kmdw_cd.KNOWN_DESTINATIONS.get(self.plan.destination, self.plan.destination)
        return {
            "kind": "check_in",
            "acid": self.acid,
            "persona": self.persona.value,
            "destination_name": dest_name,
        }

    def receive_clearance(self, tick: int, parsed) -> tuple[Optional[dict], dict]:
        """Ingest the model's clearance. Returns (verbalizer_intent, fsm_intent_event).

        ``verbalizer_intent`` is None when the pilot drops the readback (RB-DROP).
        """
        self.understood = {
            "clearance_limit": parsed.destination,
            "route": parsed.sid,
            "altitude": parsed.altitude,
            "frequency": parsed.frequency,
            "squawk": parsed.squawk,
        }
        self.readback = dict(self.understood)
        self.readback_acid = self.acid
        code = self.error.code if self.error else None

        if code == "RB-ALT":
            self.readback["altitude"] = self.error.detail.get("wrong_altitude")
        elif code == "RB-FREQ":
            self.readback["frequency"] = self.error.detail.get("wrong_frequency")
        elif code == "RB-PART":
            self.readback[self.error.detail.get("omit", "squawk")] = None
        elif code == "CS-WRONG":
            self.readback_acid = self.error.detail.get("wrong_callsign", self.acid)
        elif code == "RB-DROP":
            self.readback_dropped = True

        # The aircraft flies the accepted readback. RB-DROP/CS-WRONG are pure
        # readback artifacts (understood is correct) -> comply with understood.
        if code in (None, "RB-DROP", "CS-WRONG"):
            self.comply_as = dict(self.understood)
        else:
            self.comply_as = dict(self.readback)

        self.state = CDState.READBACK_PENDING
        self.readback_deadline = tick + self.correction_window_sec

        fsm_intent_event = {
            "acid": self.acid,
            "instruction_understood": self.understood,
            "readback_content": None if self.readback_dropped else self.readback,
            "readback_acid": self.readback_acid,
            "will_comply_as": self.comply_as,
            "error_code": code,
        }

        if self.readback_dropped:
            return None, fsm_intent_event

        rb = {
            "altitude": self.readback["altitude"],
            "frequency": self.readback["frequency"],
            "squawk": self.readback["squawk"],
        }
        verb = {
            "kind": "readback",
            "acid": self.readback_acid,
            "persona": self.persona.value,
            "readback": rb,
        }
        return verb, fsm_intent_event

    def receive_correction(self, tick: int, parsed) -> Optional[dict]:
        """Apply a controller correction if it's in-window. Returns a re-readback intent."""
        if self.state != CDState.READBACK_PENDING:
            return None
        if self.readback_deadline is not None and tick > self.readback_deadline:
            return None  # too late — no longer a catch

        corrected = False
        if parsed.altitude is not None:
            self.readback["altitude"] = parsed.altitude
            self.comply_as["altitude"] = parsed.altitude
            corrected = True
        if parsed.frequency is not None:
            self.readback["frequency"] = parsed.frequency
            self.comply_as["frequency"] = parsed.frequency
            corrected = True
        if parsed.squawk is not None:
            self.readback["squawk"] = parsed.squawk
            self.comply_as["squawk"] = parsed.squawk
            corrected = True
        # A bare prompt (e.g., re-addressing after RB-DROP or wrong callsign) also
        # closes the loop: the pilot re-reads back correctly with the right callsign.
        if self.readback_dropped or self.readback_acid != self.acid:
            self.readback_dropped = False
            self.readback_acid = self.acid
            corrected = True

        if not corrected:
            return None
        self.error_caught = True
        return {
            "kind": "correction_ack",
            "acid": self.acid,
            "persona": self.persona.value,
            "readback": {
                "altitude": self.readback.get("altitude"),
                "frequency": self.readback.get("frequency"),
                "squawk": self.readback.get("squawk"),
            },
        }

    def finalize(self, tick: int) -> dict:
        """Depart the aircraft with whatever clearance it currently holds."""
        self.state = CDState.CLEARED
        return {
            "acid": self.acid,
            "comply_as": self.comply_as,
            "understood": self.understood,
            "error_code": self.error.code if self.error else None,
            "error_caught": self.error_caught,
            "readback_dropped": self.readback_dropped,
        }
