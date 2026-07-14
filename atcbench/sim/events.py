"""Append-only event log (DESIGN §4.6).

Every entry is ``{"tick": int, "type": str, "payload": {...}}``. The log is the
single source of truth for scoring: a completed session is re-scoreable (and, given
recorded model outputs, re-runnable) from the log alone.

Serialization is deterministic — keys are sorted and floats are not reformatted —
so two runs that produce the same events produce byte-identical JSONL (§17.2).
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator


# Event type constants (non-exhaustive; DESIGN §4.6). Kept as constants so typos
# surface as import errors rather than silently-mismatched string literals.
AIRCRAFT_SPAWN = "aircraft_spawn"
RADAR_SNAPSHOT_SENT = "radar_snapshot_sent"
MODEL_TURN_START = "model_turn_start"
MODEL_TURN_END = "model_turn_end"
TRANSMISSION = "transmission"
FSM_INTENT = "fsm_intent"
COMMAND_APPLIED = "command_applied"
STRIP_OP = "strip_op"
HANDOFF_OFFERED = "handoff_offered"
HANDOFF_ACCEPTED = "handoff_accepted"
CLEARANCE_ISSUED = "clearance_issued"
CLEARANCE_CORRECTED = "clearance_corrected"
SPURIOUS_CORRECTION = "spurious_correction"
CONTROLLER_PARSE = "controller_parse"
READBACK_AFFIRMED = "readback_affirmed"
SAY_AGAIN = "say_again"
UNPARSED_MODEL_OUTPUT = "unparsed_model_output"
READBACK = "readback"
AIRCRAFT_CLEARED = "aircraft_cleared"
PILOT_DEVIATION = "pilot_deviation"
SESSION_END = "session_end"
# Ground position
TAXI_CLEARANCE = "taxi_clearance"
CROSSING_CLEARANCE = "crossing_clearance"
RUNWAY_INCURSION = "runway_incursion"
DEADLOCK = "deadlock"
AIRCRAFT_ARRIVED = "aircraft_arrived"
# Tower position
LANDING_CLEARANCE = "landing_clearance"
TAKEOFF_CLEARANCE = "takeoff_clearance"
LUAW_CLEARANCE = "luaw_clearance"
GO_AROUND = "go_around"
LANDED = "landed"
DEPARTED_SECTOR = "departed_sector"
WAKE_VIOLATION = "wake_violation"
LOS_EVENT = "los_event"


@dataclass
class Event:
    tick: int
    type: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(
            {"tick": self.tick, "type": self.type, "payload": self.payload},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )

    @staticmethod
    def from_json(line: str) -> "Event":
        obj = json.loads(line)
        return Event(tick=obj["tick"], type=obj["type"], payload=obj.get("payload", {}))


class EventLog:
    """In-memory append-only event list with deterministic JSONL persistence."""

    def __init__(self) -> None:
        self._events: list[Event] = []

    def emit(self, tick: int, type: str, **payload: Any) -> Event:
        # Snapshot the payload so the log is immutable ground truth: callers often pass
        # live mutable state (e.g. an FSM's readback dict), which they mutate later — a
        # deep copy captures the value *at emit time* (§4.6, the fsm_intent primitive).
        ev = Event(tick=tick, type=type, payload=copy.deepcopy(payload))
        self._events.append(ev)
        return ev

    def __iter__(self) -> Iterator[Event]:
        return iter(self._events)

    def __len__(self) -> int:
        return len(self._events)

    def of_type(self, *types: str) -> list[Event]:
        wanted = set(types)
        return [e for e in self._events if e.type in wanted]

    def to_jsonl(self) -> str:
        return "\n".join(e.to_json() for e in self._events) + ("\n" if self._events else "")

    def write(self, path: str | Path) -> None:
        Path(path).write_text(self.to_jsonl(), encoding="utf-8")

    @staticmethod
    def read(path: str | Path) -> "EventLog":
        log = EventLog()
        text = Path(path).read_text(encoding="utf-8")
        for line in text.splitlines():
            if line.strip():
                log._events.append(Event.from_json(line))
        return log

    @staticmethod
    def from_events(events: Iterable[Event]) -> "EventLog":
        log = EventLog()
        log._events.extend(events)
        return log
