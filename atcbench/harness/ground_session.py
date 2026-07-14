"""Ground session runner (DESIGN §4.4, §6.2).

Time-stepped at the ground radar cadence (5 sim-seconds/sweep). Each sweep: process
check-ins, hand the model turns until it waits, advance taxi kinematics one step, then
run the conflict oracle (runway incursion + head-on deadlock). Aircraft taxi their
cleared route, hold short of guarded runways until an explicit crossing clearance, and
block head-on rather than passing through each other (which is what deadlocks them).
"""

from __future__ import annotations

import json
import platform
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .. import HARNESS_VERSION
from ..charts import kmrl_gnd
from ..pilots import parser as P
from ..scenarios.gnd import GNDScenario
from ..sim import events as E
from ..sim.events import EventLog
from ..sim.taxi import GroundAircraft, occupied_edge
from ..strips.store import StripStore
from ..verbalizer import CachedVerbalizer, default_verbalizer
from .regime import TurnBased

SWEEP_SEC = 5
DEADLOCK_SEC = 300  # head-on standoff beyond this = DEADLOCK cardinal (§13.1)
CROSSING_SAFE_GAP_SEC = 40  # oracle margin before an upcoming hot window
COORD_LEAD_SEC = 45  # Tower calls ahead this long before using the runway (§4.5)
MAX_TURNS_PER_SWEEP = 40
BASE_SIM_HOUR = 14
TOWER_SPEAKER = "MRL_TWR"


def _spoken_runway(rwy: str) -> str:
    from ..verbalizer.template import spoken_digits

    words = {"l": "left", "r": "right", "c": "center"}
    return f"{spoken_digits(rwy[:-1])} {words.get(rwy[-1].lower(), rwy[-1])}"


def _sim_time(tick: int) -> str:
    h = BASE_SIM_HOUR + tick // 3600
    return f"{h:02d}:{(tick % 3600) // 60:02d}:{tick % 60:02d}Z"


@dataclass
class GroundSessionResult:
    scenario: GNDScenario
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
            json.dumps(self.scenario.to_dict(), sort_keys=True, indent=2), encoding="utf-8")
        self.log.write(d / "events.jsonl")
        (d / "transcript.jsonl").write_text(
            "".join(json.dumps(m, sort_keys=True) + "\n" for m in self.transcript), encoding="utf-8")
        (d / "strips_history.jsonl").write_text(
            "".join(json.dumps(s, sort_keys=True) + "\n" for s in self.strips_history), encoding="utf-8")
        (d / "model_io.json").write_text(
            json.dumps({"harness_version": self.harness_version,
                    "python_version": platform.python_version(), "prompt_hash": self.prompt_hash,
                        "regime": self.regime, "turns": self.model_io},
                       sort_keys=True, indent=2), encoding="utf-8")
        if self.verbalizer_cache is not None:
            (d / "verbalizer_cache.json").write_text(
                json.dumps(self.verbalizer_cache, sort_keys=True, indent=2), encoding="utf-8")


class GroundSession:
    def __init__(self, scenario: GNDScenario, verbalizer=None, prompt_hash: str = "gnd-template-v1",
                 regime=None):
        self.scn = scenario
        self.graph = kmrl_gnd.GRAPH
        self.vb = verbalizer or default_verbalizer()
        self.prompt_hash = prompt_hash
        self.regime = regime or TurnBased()
        self._think_remainder = 0.0

        self.log = EventLog()
        self.transcript: list[dict] = []
        self.strips = StripStore(bays=["inbound_taxi", "outbound_taxi", "holding_short", "crossing"])
        self.model_io: list[dict] = []

        self.tick = 0
        self.last_shown = 0
        self.active: dict[str, GroundAircraft] = {}
        self.holds: set[str] = set()  # aircraft told to hold position
        self._spawned: set[str] = set()
        self._spawns = scenario.all_spawns()
        self._deadlock_since: dict[frozenset, int] = {}
        self._incursion_fired: set[str] = set()
        self._deadlock_fired: set[frozenset] = set()
        self._arrived: list[str] = []
        self._coord_hold_sent: set[tuple[str, int]] = set()
        self._coord_release_sent: set[tuple[str, int]] = set()

    # --- channel -------------------------------------------------------------

    def _tx(self, speaker: str, text: str) -> None:
        self.transcript.append({"t": self.tick, "from": speaker, "text": text})
        self.log.emit(self.tick, E.TRANSMISSION, speaker=speaker, text=text)

    # --- spawns --------------------------------------------------------------

    def _process_spawns(self) -> None:
        for sp in self._spawns:
            if sp.acid in self._spawned or sp.call_tick > self.tick:
                continue
            self._spawned.add(sp.acid)
            ac = GroundAircraft(acid=sp.acid, actype=sp.actype, wake=sp.wake, role=sp.role,
                                spawn_node=sp.node, goal_node=sp.goal,
                                error_code=(self.scn.error_schedule.get(sp.acid).code
                                            if sp.acid in self.scn.error_schedule else None))
            self.active[sp.acid] = ac
            bay = "outbound_taxi" if sp.role == "departure" else "inbound_taxi"
            self.strips.strip_create(sp.acid, bay, {"type": sp.actype, "role": sp.role, "gate": sp.gate})
            self.log.emit(self.tick, E.AIRCRAFT_SPAWN, acid=sp.acid, role=sp.role, actype=sp.actype)
            self._tx(sp.acid, self.vb.render({"kind": "taxi_checkin", "acid": sp.acid,
                                              "persona": "airline_crisp", "role": sp.role, "gate": sp.gate}))

    # --- observation ---------------------------------------------------------

    def _holding_short_of(self, ac: GroundAircraft) -> Optional[str]:
        nxt = ac.next_node()
        if nxt is None:
            return None
        node = self.graph.node(nxt)
        if node.guard_runway and node.guard_runway not in ac.crossings_cleared:
            return node.guard_runway
        return None

    def _runway_status(self) -> dict:
        # Current status only — no future-schedule oracle (§4.5): upcoming runway use
        # is announced on frequency via scripted Tower coordination, and keeping that
        # picture is the model's job.
        return {rwy: {"hot": self.scn.is_hot(rwy, self.tick)} for rwy in self.scn.hot_windows}

    def _process_coordination(self) -> None:
        """Scripted Tower coordination calls replace the ``next_hot_in`` field: Tower
        calls ahead of each hot window and releases crossings when its traffic is done."""
        for rwy, windows in self.scn.hot_windows.items():
            for i, (ws, we) in enumerate(windows):
                key = (rwy, i)
                if key not in self._coord_hold_sent and ws - COORD_LEAD_SEC <= self.tick < we:
                    self._coord_hold_sent.add(key)
                    self._tx(TOWER_SPEAKER,
                             f"Ground, Tower, departure traffic runway {_spoken_runway(rwy)}, "
                             f"hold all crossings.")
                elif key in self._coord_hold_sent and key not in self._coord_release_sent \
                        and self.tick >= we:
                    self._coord_release_sent.add(key)
                    self._tx(TOWER_SPEAKER,
                             f"Ground, Tower, runway {_spoken_runway(rwy)} traffic complete, "
                             f"crossings at your discretion.")

    def _build_observation(self) -> dict:
        new_msgs = [dict(m) for m in self.transcript[self.last_shown:]]
        self.last_shown = len(self.transcript)
        aircraft = []
        for acid, ac in self.active.items():
            aircraft.append({
                "acid": acid, "actype": ac.actype, "role": ac.role,
                "at_node": ac.at_node(), "next_node": ac.next_node(),
                "goal": ac.goal_node, "gate": ac.goal_node if ac.role == "arrival" else None,
                "route_assigned": bool(ac.route),
                "holding_short_of": self._holding_short_of(ac),
                "crossings_cleared": sorted(ac.crossings_cleared),
                "held": acid in self.holds,
            })
        return {"tick": self.tick, "sim_time": _sim_time(self.tick), "position": self.scn.position,
                "frequency": new_msgs, "runways": self._runway_status(),
                "aircraft": aircraft, "tower_sequence": list(self.scn.tower_sequence)}

    # --- model turns ---------------------------------------------------------

    def _give_model_turns(self, adapter) -> None:
        for _ in range(MAX_TURNS_PER_SWEEP):
            obs = self._build_observation()
            self.log.emit(self.tick, E.RADAR_SNAPSHOT_SENT, n_aircraft=len(obs["aircraft"]))
            self.log.emit(self.tick, E.MODEL_TURN_START)
            resp = adapter.step(obs)
            # Verbatim I/O (§3.2): the observation sent and the output received.
            io_entry = {"tick": self.tick, "observation": obs, "output": resp}
            self.model_io.append(io_entry)
            tokens = int(resp.get("output_tokens", 0))
            self.log.emit(self.tick, E.MODEL_TURN_END, output_tokens=tokens)
            # Token-metered: taxi continues while the model thinks (§4.2). Verbose
            # deliberation lets aircraft roll toward the crossing / each other.
            self._advance_thinking(self.regime.thinking_seconds(tokens))
            waited, results = self._apply_calls(resp)
            io_entry["tool_results"] = results
            adapter.receive_tool_results(results)
            if waited:
                break
        self.strips.snapshot(self.tick)

    def _advance_thinking(self, seconds: int) -> None:
        """Convert model thinking time into extra taxi movement (SWEEP_SEC granular)."""
        if seconds <= 0:
            return
        self._think_remainder += seconds
        while self._think_remainder >= SWEEP_SEC and self.tick <= self.scn.session_seconds:
            self._think_remainder -= SWEEP_SEC
            self._step_movement()
            self.tick += SWEEP_SEC
            self._process_spawns()
            self._process_coordination()

    def _apply_calls(self, resp: dict) -> tuple[bool, list[str]]:
        """Apply a turn's tool calls; returns (waited, one result string per call)."""
        calls = resp.get("tool_calls") or []
        results: list[str] = []
        if not calls:
            # A formatting failure is not a decision to wait — log it (§7.2).
            if resp.get("text"):
                self.log.emit(self.tick, E.UNPARSED_MODEL_OUTPUT, text=resp["text"])
            return True, results
        for call in calls:
            name, inp = call.get("name"), call.get("input", {})
            if name == "wait":
                results.append("ok")
                return True, results
            if name == "transmit":
                self._handle_transmit(inp.get("text", ""))
                results.append("transmitted")
            # Strip ops are free here (CD charges 1 s each) until the shared
            # channel/cost model lands (P4.0a).
            elif name == "strip_create":
                results.append(self.strips.strip_create(inp["acid"], inp["bay"], inp.get("fields")))
            elif name == "strip_update":
                results.append(self.strips.strip_update(inp["acid"], inp.get("patch", {})))
            elif name == "strip_move":
                results.append(self.strips.strip_move(inp["acid"], inp["bay"], inp.get("index", 0)))
            elif name == "strip_delete":
                results.append(self.strips.strip_delete(inp["acid"]))
            elif name == "bay_read":
                results.append(json.dumps(self.strips.bay_read(), sort_keys=True))
            else:
                results.append(f"unknown tool: {name}")
        return False, results

    def _handle_transmit(self, text: str) -> None:
        self._tx(self.scn.position, text)
        pt = P.parse_ground_transmission(text, list(self.active.keys()))
        self.log.emit(self.tick, E.CONTROLLER_PARSE, tier=int(pt.tier),
                      tier_name=pt.tier.name, intent=pt.intent, acid=pt.acid)
        if pt.acid is None or pt.acid not in self.active:
            return  # unaddressed — logged above; nobody on frequency can respond
        ac = self.active[pt.acid]
        handled = False
        if pt.intent == "taxi":
            handled = True
            if self._assign_route(ac, pt):
                self.log.emit(self.tick, E.TAXI_CLEARANCE, acid=ac.acid, to_runway=pt.to_runway,
                              to_gate=pt.to_gate, via=pt.via, hold_short=pt.hold_short)
                self._tx(ac.acid, self.vb.render({"kind": "taxi_readback", "acid": ac.acid,
                                                  "persona": "airline_crisp", "text": self._summarize(pt)}))
            else:
                # No chart-legal route in the transmission — bare "taxi" earns no free
                # canonical route (audit M2): the pilot asks for the route instead.
                self.log.emit(self.tick, E.SAY_AGAIN, acid=ac.acid, tier=int(pt.tier),
                              intent="taxi", reason="no_route")
                self._tx(ac.acid, self.vb.render({"kind": "route_request", "acid": ac.acid,
                                                  "persona": "airline_crisp"}))
        if pt.cross:
            handled = True
            for rwy in pt.cross:
                ac.crossings_cleared.add(rwy)
            self.holds.discard(ac.acid)
            self.log.emit(self.tick, E.CROSSING_CLEARANCE, acid=ac.acid, runways=pt.cross)
            self._tx(ac.acid, self.vb.render({"kind": "taxi_readback", "acid": ac.acid,
                                              "persona": "airline_crisp",
                                              "text": "cross runway " + ", ".join(pt.cross)}))
        if pt.hold_position:
            handled = True
            self.holds.add(ac.acid)
        if not handled:
            # Addressed but unusable (§7.2 tier 3/4): pilot asks for a repeat.
            self.log.emit(self.tick, E.SAY_AGAIN, acid=ac.acid, tier=int(pt.tier),
                          intent=pt.intent)
            self._tx(ac.acid, self.vb.render({"kind": "say_again", "acid": ac.acid,
                                              "persona": "airline_crisp"}))

    def _summarize(self, pt) -> str:
        bits = []
        if pt.to_runway:
            bits.append(f"runway {pt.to_runway}")
        if pt.to_gate:
            bits.append(f"to {pt.to_gate}")
        if pt.via:
            bits.append("via " + " ".join(pt.via))
        if pt.hold_short:
            bits.append("hold short runway " + ", ".join(pt.hold_short))
        return ", ".join(bits) or "roger"

    def _assign_route(self, ac: GroundAircraft, pt) -> bool:
        """Assign a route only when the model actually transmitted one that exists in
        the chart (audit M2). Returns False when the transmission names no chart-legal
        route — pilots don't invent routes the controller never said."""
        if ac.route:  # amendment: keep current progress, only (re)assign if not moving far
            return True
        via = [v.lower() for v in pt.via]
        gate = ac.goal_node if ac.role == "arrival" else ac.spawn_node
        if ac.role == "departure":
            if pt.to_runway != kmrl_gnd.DEPARTURE_RUNWAY or "a" not in via:
                return False
            ac.route = kmrl_gnd.departure_route(gate)
        elif "a" in via:
            ac.route = kmrl_gnd.arrival_route_via_a(gate)  # misroute: flown as transmitted
        elif "b" in via:
            ac.route = kmrl_gnd.arrival_route(gate)
        else:
            return False
        ac.idx = 0
        ac.progress = 0
        return True

    # --- kinematics + conflict oracle ----------------------------------------

    def _step_movement(self) -> None:
        # Phase 1: intended next edge for each candidate mover.
        intents: dict[str, tuple[str, str]] = {}
        for acid, ac in self.active.items():
            if ac.arrived or acid in self.holds or not ac.route:
                continue
            if ac.idx + 1 >= len(ac.route):
                continue
            u, v = ac.route[ac.idx], ac.route[ac.idx + 1]
            node_v = self.graph.node(v)
            # Hold short of a guarded runway unless cleared (or dropped-hold-short error).
            if node_v.guard_runway and node_v.guard_runway not in ac.crossings_cleared:
                if ac.error_code == "GND-HS-DROP" and not ac.crossed_without_clearance:
                    ac.crossed_without_clearance = True
                    self.log.emit(self.tick, E.PILOT_DEVIATION, acid=acid, kind="crossed_without_clearance",
                                  runway=node_v.guard_runway)
                else:
                    continue
            intents[acid] = (u, v)

        # Phase 2: block head-on movers (opposing the same edge) -> potential deadlock.
        # occ maps each aircraft's currently-occupied directed edge to its acid.
        occ: dict[tuple[str, str], str] = {}
        for acid, ac in self.active.items():
            oe = occupied_edge(ac)
            if oe:
                occ[oe] = acid
        blocked: set[str] = set()
        pairs_now: set[frozenset] = set()
        items = list(intents.items())
        for i, (a, (ua, va)) in enumerate(items):
            # Blocked only if a *different* aircraft occupies the reverse edge.
            opposer = occ.get((va, ua))
            if opposer is not None and opposer != a:
                blocked.add(a)
                pairs_now.add(frozenset((a, opposer)))
            for b, (ub, vb) in items[i + 1:]:
                if (ua, va) == (vb, ub):  # both want to traverse the same segment opposite
                    blocked.add(a)
                    blocked.add(b)
                    pairs_now.add(frozenset((a, b)))

        # Phase 3: advance the unblocked.
        for acid, (u, v) in intents.items():
            if acid in blocked:
                continue
            ac = self.active[acid]
            ac.progress += 1
            if ac.progress >= self.graph.edge(u, v).transit_sweeps():
                ac.idx += 1
                ac.progress = 0

        self._track_deadlocks(pairs_now)
        self._check_incursions()
        self._check_arrivals()

    def _track_deadlocks(self, pairs_now: set[frozenset]) -> None:
        for pair in pairs_now:
            self._deadlock_since.setdefault(pair, self.tick)
        for pair in list(self._deadlock_since):
            if pair not in pairs_now:
                del self._deadlock_since[pair]
                continue
            if pair not in self._deadlock_fired and self.tick - self._deadlock_since[pair] >= DEADLOCK_SEC:
                self._deadlock_fired.add(pair)
                a, b = tuple(pair)
                self.log.emit(self.tick, E.DEADLOCK, acids=sorted([a, b]), provenance="model")

    def _check_incursions(self) -> None:
        for acid, ac in self.active.items():
            oe = occupied_edge(ac)
            if not oe:
                continue
            edge = self.graph.edge(*oe)
            if not edge or not edge.runway:
                continue
            rwy = edge.runway
            hot = self.scn.is_hot(rwy, self.tick) or any(
                self.graph.edge(*(occupied_edge(o) or ("", "")))
                and (self.graph.edge(*occupied_edge(o)).runway == rwy)
                for o in self.active.values() if o.acid != acid and occupied_edge(o))
            if hot and acid not in self._incursion_fired:
                self._incursion_fired.add(acid)
                provenance = "pilot" if ac.crossed_without_clearance else "model"
                self.log.emit(self.tick, E.RUNWAY_INCURSION, acid=acid, runway=rwy, provenance=provenance)

    def _check_arrivals(self) -> None:
        for acid, ac in list(self.active.items()):
            if ac.route and ac.idx >= len(ac.route) - 1 and not ac.arrived:
                ac.arrived = True
                self.log.emit(self.tick, E.AIRCRAFT_ARRIVED, acid=acid, role=ac.role,
                              at=ac.at_node(), goal=ac.goal_node)
                if ac.role == "arrival":
                    self.strips.strip_delete(acid)
                    del self.active[acid]

    # --- driver --------------------------------------------------------------

    def run(self, adapter) -> GroundSessionResult:
        while self.tick <= self.scn.session_seconds:
            self._process_spawns()
            self._process_coordination()
            if self.active:
                self._give_model_turns(adapter)
            self._step_movement()
            self.tick += SWEEP_SEC
            if not self._spawns_remaining() and not self._movers_remaining():
                break
        self.log.emit(self.tick, E.SESSION_END, position=self.scn.position, seed=self.scn.seed)
        vb_cache = self.vb.cache.to_dict() if isinstance(self.vb, CachedVerbalizer) else None
        return GroundSessionResult(
            scenario=self.scn, log=self.log, transcript=self.transcript,
            strips_history=self.strips.history, model_io=self.model_io,
            prompt_hash=self.prompt_hash, verbalizer_cache=vb_cache, regime=self.regime.name)

    def _spawns_remaining(self) -> bool:
        return len(self._spawned) < len(self._spawns)

    def _movers_remaining(self) -> bool:
        return any(not ac.arrived for ac in self.active.values())
