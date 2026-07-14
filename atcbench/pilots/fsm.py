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
        self.blocked_readback: bool = False  # BLOCKED: readback stepped on, unreadable
        self.confused: bool = False  # CS-CONF: holding a clearance meant for a twin
        self._say_again_used: bool = False
        # CS-CONF only fires if this aircraft actually intercepts the twin's clearance;
        # every other class triggers the moment the clearance arrives.
        self.error_triggered: bool = error is not None and error.code != "CS-CONF"
        self.readback_deadline: Optional[int] = None

    # --- transitions ---------------------------------------------------------

    def check_in(self, tick: int) -> dict:
        self.state = CDState.AWAITING_CLEARANCE
        from ..charts import kmrl_cd

        dest_name = kmrl_cd.KNOWN_DESTINATIONS.get(self.plan.destination, self.plan.destination)
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
        code = self.error.code if self.error else None

        # SAY-AGAIN (§8.3): the pilot didn't catch it and asks for a repeat — the
        # aircraft stays uncleared until the controller transmits the clearance again.
        if code == "SAY-AGAIN" and not self._say_again_used:
            self._say_again_used = True
            fsm_event = {"acid": self.acid, "instruction_understood": None,
                         "readback_content": None, "readback_acid": self.acid,
                         "will_comply_as": None, "error_code": code}
            return ({"kind": "say_again", "acid": self.acid,
                     "persona": self.persona.value}, fsm_event)
        if code == "SAY-AGAIN" and self._say_again_used and not self.error_caught:
            self.error_caught = True  # the controller repeated the clearance: caught

        self.understood = {
            "clearance_limit": parsed.destination,
            "route": parsed.sid,
            "altitude": parsed.altitude,
            "frequency": parsed.frequency,
            "squawk": parsed.squawk,
        }
        self.readback = dict(self.understood)
        self.readback_acid = self.acid

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
        elif code == "BLOCKED":
            self.blocked_readback = True  # readback transmitted but stepped on

        # The aircraft flies the accepted readback. RB-DROP/CS-WRONG/BLOCKED/SAY-AGAIN
        # are readback/channel artifacts (understood is correct) -> comply understood.
        if code in (None, "RB-DROP", "CS-WRONG", "BLOCKED", "SAY-AGAIN"):
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
        if self.blocked_readback:
            # The readback goes out but is stepped on — the controller hears squeal.
            return ({"kind": "blocked_noise", "acid": self.acid,
                     "persona": self.persona.value}, fsm_intent_event)

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

    def intercept_clearance(self, tick: int, parsed) -> tuple[dict, dict]:
        """CS-CONF (§8.4): this aircraft takes a clearance addressed to its twin,
        reads it back under its OWN callsign, and will fly it unless the controller
        notices the wrong voice answered and disregards it in the window."""
        self.error_triggered = True
        self.confused = True
        self.understood = {
            "clearance_limit": parsed.destination,
            "route": parsed.sid,
            "altitude": parsed.altitude,
            "frequency": parsed.frequency,
            "squawk": parsed.squawk,
        }
        self.readback = dict(self.understood)
        self.comply_as = dict(self.understood)  # the twin's clearance, flown by us
        self.readback_acid = self.acid
        self.state = CDState.READBACK_PENDING
        self.readback_deadline = tick + self.correction_window_sec
        fsm_event = {
            "acid": self.acid,
            "instruction_understood": self.understood,
            "readback_content": self.readback,
            "readback_acid": self.acid,
            "will_comply_as": self.comply_as,
            "error_code": "CS-CONF",
            "confused_with": parsed.acid,
        }
        verb = {"kind": "readback", "acid": self.acid, "persona": self.persona.value,
                "readback": {"altitude": self.readback["altitude"],
                             "frequency": self.readback["frequency"],
                             "squawk": self.readback["squawk"]}}
        return verb, fsm_event

    def receive_correction(self, tick: int, parsed) -> tuple[Optional[dict], bool]:
        """Apply a controller correction if it's in-window.

        Returns ``(ack_intent, spurious)``. An error counts as *caught* only when a
        scheduled error was live and the correction addressed the erroneous element —
        blind re-clearance of a correct readback is a false alarm, not hearback, and
        comes back flagged ``spurious`` (§13.3).
        """
        if self.state != CDState.READBACK_PENDING:
            return None, False
        if self.readback_deadline is not None and tick > self.readback_deadline:
            return None, False  # too late — no longer a catch

        if self.confused:
            # CS-CONF: any in-window re-address makes the twin drop the clearance it
            # took by mistake and go back to waiting for its own.
            self.confused = False
            self.state = CDState.AWAITING_CLEARANCE
            self.understood, self.readback, self.comply_as = {}, {}, {}
            self.readback_deadline = None
            self.error_caught = True
            return ({"kind": "standby_ack", "acid": self.acid,
                     "persona": self.persona.value}, False)

        touched: set[str] = set()
        if parsed.altitude is not None:
            self.readback["altitude"] = parsed.altitude
            self.comply_as["altitude"] = parsed.altitude
            touched.add("altitude")
        if parsed.frequency is not None:
            self.readback["frequency"] = parsed.frequency
            self.comply_as["frequency"] = parsed.frequency
            touched.add("frequency")
        if parsed.squawk is not None:
            self.readback["squawk"] = parsed.squawk
            self.comply_as["squawk"] = parsed.squawk
            touched.add("squawk")
        # A bare prompt (e.g., re-addressing after RB-DROP, a blocked readback, or a
        # wrong callsign) closes the loop: the pilot re-reads back correctly.
        bare_prompt = False
        if self.readback_dropped or self.blocked_readback or self.readback_acid != self.acid:
            self.readback_dropped = False
            self.blocked_readback = False
            self.readback_acid = self.acid
            bare_prompt = True

        if not touched and not bare_prompt:
            return None, False

        code = self.error.code if self.error else None
        was_caught = self.error_caught
        if code in ("RB-ALT", "RB-FREQ"):
            addressed = {"RB-ALT": "altitude", "RB-FREQ": "frequency"}[code] in touched
        elif code == "RB-PART":
            addressed = self.error.detail.get("omit", "squawk") in touched
        elif code in ("RB-DROP", "CS-WRONG", "BLOCKED"):
            addressed = True  # any in-window re-address closes the loop
        else:
            addressed = False
        if addressed and not was_caught:
            self.error_caught = True
        # Spurious: nothing was wrong (no scheduled error, or already corrected).
        spurious = code is None or was_caught

        return {
            "kind": "correction_ack",
            "acid": self.acid,
            "persona": self.persona.value,
            "readback": {
                "altitude": self.readback.get("altitude"),
                "frequency": self.readback.get("frequency"),
                "squawk": self.readback.get("squawk"),
            },
        }, spurious

    def finalize(self, tick: int) -> dict:
        """Depart the aircraft with whatever clearance it currently holds."""
        self.state = CDState.CLEARED
        return {
            "acid": self.acid,
            "comply_as": self.comply_as,
            "understood": self.understood,
            "error_code": self.error.code if self.error else None,
            "error_caught": self.error_caught,
            "error_triggered": self.error_triggered,
            "readback_dropped": self.readback_dropped,
        }
