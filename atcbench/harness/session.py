"""Clearance Delivery session runner (DESIGN §4.1 event-driven cadence, §7.1 channel).

Turn-based regime: the sim pauses while the model reasons, but transmissions consume
sim time at 150 wpm (frequency physics). The runner alternates between advancing the
sim to the next scheduled event (pilot check-in, readback correction deadline, or a
neglect deadline) and handing the model turns until it waits.

Everything the scorer needs is emitted to the event log; the run directory is
self-contained and re-scoreable / re-runnable (given recorded model outputs).
"""

from __future__ import annotations

import json
import platform
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .. import HARNESS_VERSION
from ..charts import kmrl_cd
from ..pilots import parser as P
from ..pilots.fsm import CDState, PilotFSM
from ..scenarios.cd import Scenario
from ..sim import events as E
from ..sim.events import EventLog
from ..strips.store import StripStore
from ..verbalizer import CachedVerbalizer, default_verbalizer
from .channel import FrequencyChannel
from .regime import TurnBased

NEGLECT_THRESHOLD_SEC = 180  # CD: unanswered/uncleared beyond this = NEGLECT (§13.1)
MAX_TURNS_PER_WINDOW = 60
BASE_SIM_HOUR = 14  # sessions start at 14:00:00Z (deterministic wall-clock label)


def _sim_time(tick: int) -> str:
    total = tick
    h = BASE_SIM_HOUR + total // 3600
    m = (total % 3600) // 60
    s = total % 60
    return f"{h:02d}:{m:02d}:{s:02d}Z"


@dataclass
class SessionResult:
    scenario: Scenario
    log: EventLog
    transcript: list[dict]
    strips_history: list[dict]
    model_io: list[dict]
    prompt_hash: str
    harness_version: str = HARNESS_VERSION
    verbalizer_cache: Optional[dict] = None
    regime: str = "turn"

    def write(self, out_dir: str | Path) -> None:
        d = Path(out_dir)
        d.mkdir(parents=True, exist_ok=True)
        (d / "scenario.json").write_text(
            json.dumps(self.scenario.to_dict(), sort_keys=True, indent=2), encoding="utf-8"
        )
        self.log.write(d / "events.jsonl")
        (d / "transcript.jsonl").write_text(
            "".join(json.dumps(m, sort_keys=True) + "\n" for m in self.transcript),
            encoding="utf-8",
        )
        (d / "strips_history.jsonl").write_text(
            "".join(json.dumps(s, sort_keys=True) + "\n" for s in self.strips_history),
            encoding="utf-8",
        )
        (d / "model_io.json").write_text(
            json.dumps(
                {
                    "harness_version": self.harness_version,
                    "python_version": platform.python_version(),
                    "prompt_hash": self.prompt_hash,
                    "regime": self.regime,
                    "turns": self.model_io,
                },
                sort_keys=True,
                indent=2,
            ),
            encoding="utf-8",
        )
        if self.verbalizer_cache is not None:
            (d / "verbalizer_cache.json").write_text(
                json.dumps(self.verbalizer_cache, sort_keys=True, indent=2), encoding="utf-8"
            )


class CDSession:
    def __init__(
        self,
        scenario: Scenario,
        verbalizer=None,
        correction_window_sec: int = 30,
        prompt_hash: str = "cd-template-v1",
        regime=None,
    ):
        self.scn = scenario
        self.pack = scenario.chart_pack
        self.vb = verbalizer or default_verbalizer()
        self.correction_window_sec = correction_window_sec
        self.prompt_hash = prompt_hash
        self.regime = regime or TurnBased()

        self.log = EventLog()
        self.transcript: list[dict] = []
        self.strips = StripStore(bays=["queue", "cleared", "watch"])
        self.model_io: list[dict] = []

        self.tick = 0
        self.channel = FrequencyChannel()
        self.last_shown = 0
        self.active: dict[str, PilotFSM] = {}
        self.neglect_deadline: dict[str, int] = {}
        self._checkins = sorted(scenario.flight_plans, key=lambda p: p.call_tick)
        self._ci_idx = 0
        self._total_turns = 0

    # --- channel -------------------------------------------------------------

    def _emit_transmission(self, speaker: str, text: str) -> None:
        start, end = self.channel.transmit(self.tick, text)
        self.tick = end  # event-driven clock rides the broadcast (§4.1)
        self.transcript.append({"t": start, "from": speaker, "text": text})
        self.log.emit(start, E.TRANSMISSION, speaker=speaker, text=text, start_tick=start, end_tick=end)

    # --- scheduled events ----------------------------------------------------

    def _process_checkins(self) -> None:
        while self._ci_idx < len(self._checkins) and self._checkins[self._ci_idx].call_tick <= self.tick:
            fp = self._checkins[self._ci_idx]
            self._ci_idx += 1
            self.log.emit(fp.call_tick, E.AIRCRAFT_SPAWN, acid=fp.acid, actype=fp.actype,
                          destination=fp.destination, persona=fp.persona.value)
            fsm = PilotFSM(fp, self.scn.error_schedule.get(fp.acid), self.correction_window_sec)
            self.active[fp.acid] = fsm
            self.neglect_deadline[fp.acid] = fp.call_tick + NEGLECT_THRESHOLD_SEC
            # Auto-strip on check-in (queue bay).
            self.strips.strip_create(fp.acid, "queue", {"type": fp.actype, "dest": fp.destination})
            self.log.emit(self.tick, E.STRIP_OP, op="auto_create", acid=fp.acid, bay="queue")
            intent = fsm.check_in(self.tick)
            self._emit_transmission(fp.acid, self.vb.render(intent))

    def _process_deadlines(self) -> None:
        for acid, fsm in list(self.active.items()):
            neglected = (
                fsm.state == CDState.AWAITING_CLEARANCE
                and self.tick >= self.neglect_deadline.get(acid, 1 << 30)
            )
            expired = (
                fsm.state == CDState.READBACK_PENDING
                and fsm.readback_deadline is not None
                and self.tick >= fsm.readback_deadline
            )
            if neglected or expired:
                final = fsm.finalize(self.tick)
                final["neglected"] = bool(neglected)
                self.log.emit(self.tick, E.AIRCRAFT_CLEARED, **final)
                self.strips.strip_move(acid, "cleared", 0)
                self.log.emit(self.tick, E.STRIP_OP, op="auto_move", acid=acid, bay="cleared")
                del self.active[acid]

    def _next_event_tick(self) -> Optional[int]:
        candidates: list[int] = []
        if self._ci_idx < len(self._checkins):
            candidates.append(self._checkins[self._ci_idx].call_tick)
        for acid, fsm in self.active.items():
            if fsm.state == CDState.READBACK_PENDING and fsm.readback_deadline is not None:
                candidates.append(fsm.readback_deadline)
            elif fsm.state == CDState.AWAITING_CLEARANCE:
                candidates.append(self.neglect_deadline.get(acid, 1 << 30))
        return min(candidates) if candidates else None

    # --- model turns ---------------------------------------------------------

    def _build_observation(self) -> dict:
        new_msgs = [dict(m) for m in self.transcript[self.last_shown:]]
        self.last_shown = len(self.transcript)
        aircraft = []
        for acid, fsm in self.active.items():
            status = {
                CDState.AWAITING_CLEARANCE: "awaiting_clearance",
                CDState.READBACK_PENDING: "readback_pending",
            }.get(fsm.state, "cleared")
            # No ground-truth leaks (§11.2): the filed plan is presented as filed —
            # spotting a flawed filing is the skill under test, not a provided hint —
            # and readbacks live only in the frequency feed, never pre-parsed.
            aircraft.append({
                "acid": acid,
                "actype": fsm.plan.actype,
                "status": status,
                "filed": {
                    "destination": fsm.plan.destination,
                    "destination_name": kmrl_cd.KNOWN_DESTINATIONS.get(
                        fsm.plan.destination, fsm.plan.destination
                    ),
                    "sid": fsm.plan.filed_sid,
                    "altitude": fsm.plan.filed_altitude,
                },
                "assigned_squawk": self.scn.expected_clearance[acid]["squawk"],
            })
        return {
            "tick": self.tick,
            "sim_time": _sim_time(self.tick),
            "position": self.scn.position,
            "frequency": new_msgs,
            "aircraft": aircraft,
        }

    def _give_model_turns(self, adapter) -> None:
        for _ in range(MAX_TURNS_PER_WINDOW):
            self._total_turns += 1
            obs = self._build_observation()
            self.log.emit(self.tick, E.RADAR_SNAPSHOT_SENT, n_aircraft=len(obs["aircraft"]))
            self.log.emit(self.tick, E.MODEL_TURN_START)
            resp = adapter.step(obs)
            # Verbatim I/O (§3.2): the observation sent and the output received.
            io_entry = {"tick": self.tick, "observation": obs, "output": resp}
            self.model_io.append(io_entry)
            tokens = int(resp.get("output_tokens", 0))
            self.log.emit(self.tick, E.MODEL_TURN_END, output_tokens=tokens)
            # Token-metered: the world advances while the model "thinks" (§4.2). A slow
            # model can watch a readback correction window close before it acts.
            self._advance_thinking(self.regime.thinking_seconds(tokens))
            waited, results = self._apply_tool_calls(resp)
            io_entry["tool_results"] = results
            adapter.receive_tool_results(results)
            self.strips.snapshot(self.tick)
            if waited:
                break

    def _advance_thinking(self, seconds: int) -> None:
        if seconds <= 0:
            return
        target = self.tick + seconds
        while True:
            nxt = self._next_event_tick()
            if nxt is None or nxt > target:
                break
            self.tick = nxt
            self._process_checkins()
            self._process_deadlines()
        if target > self.tick:
            self.tick = target

    def _apply_tool_calls(self, resp: dict) -> tuple[bool, list[str]]:
        """Apply a turn's tool calls; returns (waited, one result string per call)."""
        calls = resp.get("tool_calls") or []
        results: list[str] = []
        if not calls:
            # A formatting failure is not a decision to wait — log it so parsing
            # trouble is auditable instead of scored as silent inaction (§7.2).
            if resp.get("text"):
                self.log.emit(self.tick, E.UNPARSED_MODEL_OUTPUT, text=resp["text"])
            return True, results
        for call in calls:
            name = call.get("name")
            inp = call.get("input", {})
            if name == "wait":
                results.append("ok")
                return True, results
            if name == "transmit":
                self._handle_transmit(inp.get("text", ""))
                results.append("transmitted")
            elif name == "strip_create":
                results.append(self.strips.strip_create(inp["acid"], inp["bay"], inp.get("fields")))
                self.tick += 1
                self.log.emit(self.tick, E.STRIP_OP, op="create", acid=inp.get("acid"))
            elif name == "strip_update":
                results.append(self.strips.strip_update(inp["acid"], inp.get("patch", {})))
                self.tick += 1
                self.log.emit(self.tick, E.STRIP_OP, op="update", acid=inp.get("acid"))
            elif name == "strip_move":
                results.append(self.strips.strip_move(inp["acid"], inp["bay"], inp.get("index", 0)))
                self.tick += 1
                self.log.emit(self.tick, E.STRIP_OP, op="move", acid=inp.get("acid"))
            elif name == "strip_delete":
                results.append(self.strips.strip_delete(inp["acid"]))
                self.tick += 1
                self.log.emit(self.tick, E.STRIP_OP, op="delete", acid=inp.get("acid"))
            elif name == "bay_read":
                # 0 s, no state change — but the model gets its memory back (§9.3).
                results.append(json.dumps(self.strips.bay_read(), sort_keys=True))
            else:
                results.append(f"unknown tool: {name}")
        return False, results

    def _handle_transmit(self, text: str) -> None:
        self._emit_transmission(self.scn.position, text)
        parsed = P.parse_controller_transmission(text, list(self.active.keys()), self.pack)
        self.log.emit(self.tick, E.CONTROLLER_PARSE, tier=int(parsed.tier),
                      tier_name=parsed.tier.name, intent=parsed.intent, acid=parsed.acid)
        acid = parsed.acid
        if acid is None or acid not in self.active:
            return  # unaddressed — logged above; nobody on frequency can respond
        fsm = self.active[acid]
        if parsed.intent in ("affirm", "say_again"):
            # Affirming a pending readback closes the loop purposefully (§13.3);
            # otherwise acknowledged silently — the pilot has nothing to re-read.
            if parsed.intent == "affirm" and fsm.state == CDState.READBACK_PENDING:
                self.log.emit(self.tick, E.READBACK_AFFIRMED, acid=acid)
            return
        if parsed.intent == "clearance" and fsm.state == CDState.AWAITING_CLEARANCE:
            self.log.emit(self.tick, E.CLEARANCE_ISSUED, acid=acid,
                          altitude=parsed.altitude, frequency=parsed.frequency,
                          squawk=parsed.squawk, route=parsed.sid, destination=parsed.destination)
            # CS-CONF (§8.4): a similar-callsign twin may take this clearance instead.
            twin = self._csconf_interceptor(acid)
            if twin is not None:
                verb_intent, fsm_event = twin.intercept_clearance(self.tick, parsed)
                self.log.emit(self.tick, E.FSM_INTENT, **fsm_event)
                self._emit_transmission(twin.acid, self.vb.render(verb_intent))
                self.log.emit(self.tick, E.READBACK, acid=twin.acid, readback=twin.readback,
                              readback_acid=twin.acid, confused_with=acid)
                return  # the addressed aircraft never heard its clearance
            verb_intent, fsm_event = fsm.receive_clearance(self.tick, parsed)
            self.log.emit(self.tick, E.FSM_INTENT, **fsm_event)
            if fsm.state == CDState.AWAITING_CLEARANCE:
                # SAY-AGAIN: the pilot asked for a repeat and remains uncleared; the
                # neglect clock refreshes so the repeat is judged on its own timing.
                self.log.emit(self.tick, E.SAY_AGAIN, acid=acid, tier=int(parsed.tier),
                              intent="clearance", reason="pilot_request")
                self.neglect_deadline[acid] = self.tick + NEGLECT_THRESHOLD_SEC
                self._emit_transmission(acid, self.vb.render(verb_intent))
            elif verb_intent is not None:
                self._emit_transmission(acid, self.vb.render(verb_intent))
                self.log.emit(self.tick, E.READBACK, acid=acid, readback=fsm.readback,
                              readback_acid=fsm.readback_acid)
            else:
                self.log.emit(self.tick, E.PILOT_DEVIATION, acid=acid, kind="readback_dropped")
        elif fsm.state == CDState.READBACK_PENDING and parsed.intent in ("correction", "clearance"):
            ack, spurious = fsm.receive_correction(self.tick, parsed)
            if ack is not None and ack.get("kind") == "standby_ack":
                # CS-CONF caught: the twin dropped the foreign clearance and is
                # waiting again — refresh its neglect clock accordingly.
                self.neglect_deadline[acid] = self.tick + NEGLECT_THRESHOLD_SEC
            if ack is not None:
                if spurious:
                    self.log.emit(self.tick, E.SPURIOUS_CORRECTION, acid=acid)
                else:
                    self.log.emit(self.tick, E.CLEARANCE_CORRECTED, acid=acid,
                                  caught=fsm.error_caught)
                self._emit_transmission(acid, self.vb.render(ack))
                self.log.emit(self.tick, E.READBACK, acid=acid, readback=fsm.readback,
                              readback_acid=fsm.readback_acid, corrected=True)
        else:
            # Addressed but unusable for this aircraft's state (§7.2 tier 3/4):
            # the pilot asks for a repeat instead of the harness dropping it silently.
            self._say_again(acid, fsm.persona.value, parsed)

    def _csconf_interceptor(self, target_acid: str):
        """The twin that takes a clearance addressed to ``target_acid``, if its
        scheduled CS-CONF confusion is armed and it is on frequency waiting."""
        for fsm in self.active.values():
            if (fsm.error is not None and fsm.error.code == "CS-CONF"
                    and fsm.error.detail.get("confused_with") == target_acid
                    and fsm.state == CDState.AWAITING_CLEARANCE
                    and not fsm.error_triggered):
                return fsm
        return None

    def _say_again(self, acid: str, persona: str, parsed) -> None:
        self.log.emit(self.tick, E.SAY_AGAIN, acid=acid, tier=int(parsed.tier),
                      intent=parsed.intent)
        self._emit_transmission(acid, self.vb.render(
            {"kind": "say_again", "acid": acid, "persona": persona}))

    # --- driver --------------------------------------------------------------

    def run(self, adapter) -> SessionResult:
        adapter.brief(self.pack.to_dict())  # position briefing: the chart pack (§11.3)
        while True:
            nxt = self._next_event_tick()
            if nxt is None and not self.active:
                break
            if nxt is not None and nxt > self.tick:
                self.tick = nxt
            self._process_checkins()
            self._process_deadlines()
            if not self.active and self._ci_idx >= len(self._checkins):
                break
            if self.active:
                self._give_model_turns(adapter)
            if self._total_turns > 100000:  # pragma: no cover - runaway guard
                break
        self.log.emit(self.tick, E.SESSION_END, position=self.scn.position, seed=self.scn.seed)
        vb_cache = self.vb.cache.to_dict() if isinstance(self.vb, CachedVerbalizer) else None
        return SessionResult(
            scenario=self.scn,
            log=self.log,
            transcript=self.transcript,
            strips_history=self.strips.history,
            model_io=self.model_io,
            prompt_hash=self.prompt_hash,
            verbalizer_cache=vb_cache,
            regime=self.regime.name,
        )
