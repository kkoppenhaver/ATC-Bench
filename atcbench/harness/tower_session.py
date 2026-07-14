"""Tower session runner (DESIGN §6.3, §4.5).

Time-stepped at the Tower radar cadence (5 s/sweep). The first position with airborne
kinematics: arrivals close a 1-D final at type approach speed; departures roll, get
airborne, and are handed to Approach. The safety oracle enforces single-runway
occupancy (landing rollout, takeoff roll, and line-up-and-wait all occupy) and wake
separation between successive runway uses. Arrivals not cleared in time — or that reach
the threshold with the runway occupied — go around (attributed to the model), while a
seeded blown-tire go-around is attributed to the environment.
"""

from __future__ import annotations

import json
import platform
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .. import HARNESS_VERSION
from ..charts import kmrl_twr
from ..pilots import parser as P
from ..scenarios.twr import TWRScenario
from ..sim import events as E
from ..sim.events import EventLog
from ..sim.performance import perf, wake_min_sec
from ..strips.store import StripStore
from ..verbalizer import CachedVerbalizer, default_verbalizer
from .regime import TurnBased

SWEEP_SEC = 5
MAX_TURNS_PER_SWEEP = 40
BASE_SIM_HOUR = 14


def _sim_time(tick: int) -> str:
    h = BASE_SIM_HOUR + tick // 3600
    return f"{h:02d}:{(tick % 3600) // 60:02d}:{tick % 60:02d}Z"


@dataclass
class TowerAircraft:
    acid: str
    actype: str
    wake: str
    role: str
    dist_nm: float = 0.0
    phase: str = "final"          # arrivals: final/cleared_land/landing/vacated/go_around
    #                               departures: hold_short/luaw/takeoff/airborne/departed
    cleared: bool = False
    occupy_until: int = 0
    reentry_at: int = 0
    forced_done: bool = False
    approaches: int = 0

    def on_runway(self) -> bool:
        return self.phase in ("landing", "luaw", "takeoff")


@dataclass
class TowerSessionResult:
    scenario: TWRScenario
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


class TowerSession:
    def __init__(self, scenario: TWRScenario, verbalizer=None, prompt_hash: str = "twr-template-v1",
                 regime=None, representation: str = "raw"):
        self.scn = scenario
        self.vb = verbalizer or default_verbalizer()
        self.prompt_hash = prompt_hash
        self.regime = regime or TurnBased()
        self.representation = representation  # "raw" (default) | "enriched" (§11.2)
        self._think_remainder = 0.0

        self.log = EventLog()
        self.transcript: list[dict] = []
        self.strips = StripStore(bays=["arrivals", "departures", "watch"])
        self.model_io: list[dict] = []

        self.tick = 0
        self.last_shown = 0
        self.active: dict[str, TowerAircraft] = {}
        self._spawned: set[str] = set()
        self._spawns = scenario.all_spawns()
        self._forced = set(scenario.forced_go_arounds)
        self.rw_last_use_start: Optional[int] = None
        self.rw_last_wake: str = "L"
        self._los_fired: set[frozenset] = set()

    # --- channel + spawns ----------------------------------------------------

    def _tx(self, speaker: str, text: str) -> None:
        self.transcript.append({"t": self.tick, "from": speaker, "text": text})
        self.log.emit(self.tick, E.TRANSMISSION, speaker=speaker, text=text)

    def _process_spawns(self) -> None:
        for sp in self._spawns:
            if sp.acid in self._spawned or sp.call_tick > self.tick:
                continue
            self._spawned.add(sp.acid)
            ac = TowerAircraft(acid=sp.acid, actype=sp.actype, wake=sp.wake, role=sp.role,
                               dist_nm=sp.start_dist_nm,
                               phase="final" if sp.role == "arrival" else "hold_short")
            self.active[sp.acid] = ac
            bay = "arrivals" if sp.role == "arrival" else "departures"
            self.strips.strip_create(sp.acid, bay, {"type": sp.actype, "wake": sp.wake})
            self.log.emit(self.tick, E.AIRCRAFT_SPAWN, acid=sp.acid, role=sp.role, actype=sp.actype)
            miles = round(sp.start_dist_nm) if sp.role == "arrival" else None
            self._tx(sp.acid, self.vb.render({"kind": "tower_checkin", "acid": sp.acid,
                                              "persona": "airline_crisp", "role": sp.role, "miles": miles}))

    # --- observation ---------------------------------------------------------

    def _runway_occupant(self) -> Optional[str]:
        for acid, ac in self.active.items():
            if ac.on_runway():
                return acid
        return None

    def _runway_free(self, excluding: Optional[str] = None) -> bool:
        return all(not ac.on_runway() for acid, ac in self.active.items() if acid != excluding)

    def _build_observation(self) -> dict:
        new_msgs = [dict(m) for m in self.transcript[self.last_shown:]]
        self.last_shown = len(self.transcript)
        occ = self._runway_occupant()
        aircraft = []
        for acid, ac in self.active.items():
            entry = {"acid": acid, "actype": ac.actype, "wake": ac.wake, "role": ac.role,
                     "phase": ac.phase}
            if ac.role == "arrival":
                entry["dist_nm"] = round(ac.dist_nm, 2)
                entry["cleared_to_land"] = ac.phase == "cleared_land"
            aircraft.append(entry)
        # Raw representation shows only what the tower cab sees now: who is on the
        # runway. The wake/occupancy timing picture is the model's to maintain (§4.5);
        # the derived fields exist only on the enriched track (§11.2, P4.6).
        runway: dict = {"id": kmrl_twr.RUNWAY, "occupied_by": occ}
        if self.representation == "enriched":
            since = (self.tick - self.rw_last_use_start) if self.rw_last_use_start is not None else None
            runway["since_last_use_sec"] = since
            runway["last_use_wake"] = self.rw_last_wake
        return {
            "tick": self.tick, "sim_time": _sim_time(self.tick), "position": self.scn.position,
            "frequency": new_msgs,
            "runway": runway,
            "aircraft": aircraft,
        }

    # --- model turns ---------------------------------------------------------

    def _give_model_turns(self, adapter) -> None:
        for _ in range(MAX_TURNS_PER_SWEEP):
            obs = self._build_observation()
            self.log.emit(self.tick, E.RADAR_SNAPSHOT_SENT, n_aircraft=len(obs["aircraft"]))
            self.log.emit(self.tick, E.MODEL_TURN_START)
            resp = adapter.step(obs)
            self.model_io.append({"tick": self.tick, "output": resp})
            tokens = int(resp.get("output_tokens", 0))
            self.log.emit(self.tick, E.MODEL_TURN_END, output_tokens=tokens)
            self._advance_thinking(self.regime.thinking_seconds(tokens))
            if self._apply_calls(resp):
                break
        self.strips.snapshot(self.tick)

    def _advance_thinking(self, seconds: int) -> None:
        if seconds <= 0:
            return
        self._think_remainder += seconds
        while self._think_remainder >= SWEEP_SEC and self.tick <= self.scn.session_seconds:
            self._think_remainder -= SWEEP_SEC
            self._step_kinematics()
            self.tick += SWEEP_SEC
            self._process_spawns()

    def _apply_calls(self, resp: dict) -> bool:
        calls = resp.get("tool_calls") or []
        if not calls:
            # A formatting failure is not a decision to wait — log it (§7.2).
            if resp.get("text"):
                self.log.emit(self.tick, E.UNPARSED_MODEL_OUTPUT, text=resp["text"])
            return True
        for call in calls:
            name, inp = call.get("name"), call.get("input", {})
            if name == "wait":
                return True
            if name == "transmit":
                self._handle_transmit(inp.get("text", ""))
            elif name == "strip_create":
                self.strips.strip_create(inp["acid"], inp["bay"], inp.get("fields"))
            elif name == "strip_update":
                self.strips.strip_update(inp["acid"], inp.get("patch", {}))
            elif name == "strip_move":
                self.strips.strip_move(inp["acid"], inp["bay"], inp.get("index", 0))
            elif name == "strip_delete":
                self.strips.strip_delete(inp["acid"])
        return False

    def _handle_transmit(self, text: str) -> None:
        self._tx(self.scn.position, text)
        pt = P.parse_tower_transmission(text, list(self.active.keys()))
        self.log.emit(self.tick, E.CONTROLLER_PARSE, tier=int(pt.tier),
                      tier_name=pt.tier.name, intent=pt.intent, acid=pt.acid)
        if pt.acid is None or pt.acid not in self.active:
            return  # unaddressed — logged above; nobody on frequency can respond
        ac = self.active[pt.acid]
        if pt.intent == "land" and ac.role == "arrival" and ac.phase == "final":
            ac.phase = "cleared_land"
            ac.cleared = True
            self.log.emit(self.tick, E.LANDING_CLEARANCE, acid=ac.acid)
            self._tx(ac.acid, self.vb.render({"kind": "tower_readback", "acid": ac.acid,
                                              "persona": "airline_crisp",
                                              "text": "cleared to land runway three one center"}))
        elif pt.intent == "luaw" and ac.role == "departure" and ac.phase == "hold_short":
            ac.phase = "luaw"
            self.log.emit(self.tick, E.LUAW_CLEARANCE, acid=ac.acid)
            self._tx(ac.acid, self.vb.render({"kind": "tower_readback", "acid": ac.acid,
                                              "persona": "airline_crisp",
                                              "text": "line up and wait runway three one center"}))
        elif pt.intent == "takeoff" and ac.role == "departure" and ac.phase in ("hold_short", "luaw"):
            ac.phase = "takeoff"
            ac.occupy_until = self.tick + kmrl_twr.OCCUPY_DEP_SEC
            self._begin_use(ac)
            self.log.emit(self.tick, E.TAKEOFF_CLEARANCE, acid=ac.acid)
            self._tx(ac.acid, self.vb.render({"kind": "tower_readback", "acid": ac.acid,
                                              "persona": "airline_crisp",
                                              "text": "cleared for takeoff runway three one center"}))
        elif pt.intent == "go_around" and ac.role == "arrival" and ac.phase in ("final", "cleared_land"):
            self._go_around(ac, provenance="model", commanded=True)
        elif pt.intent == "handoff" and ac.role == "departure" and ac.phase == "airborne":
            ac.phase = "departed"
            self.log.emit(self.tick, E.DEPARTED_SECTOR, acid=ac.acid)
            self.strips.strip_delete(ac.acid)
            del self.active[ac.acid]
        else:
            # Addressed but unusable for this aircraft's role/phase (§7.2 tier 3/4):
            # the pilot asks for a repeat instead of the harness dropping it silently.
            self.log.emit(self.tick, E.SAY_AGAIN, acid=ac.acid, tier=int(pt.tier),
                          intent=pt.intent)
            self._tx(ac.acid, self.vb.render({"kind": "say_again", "acid": ac.acid,
                                              "persona": "airline_crisp"}))

    # --- kinematics + safety oracle ------------------------------------------

    def _begin_use(self, ac: TowerAircraft) -> None:
        """Record a runway use and check wake separation from the previous use."""
        if self.rw_last_use_start is not None:
            required = wake_min_sec(self.rw_last_wake, ac.wake)
            gap = self.tick - self.rw_last_use_start
            if gap < required:
                self.log.emit(self.tick, E.WAKE_VIOLATION, acid=ac.acid,
                              leader_wake=self.rw_last_wake, follower_wake=ac.wake,
                              gap=gap, required=required, provenance="model")
        self.rw_last_use_start = self.tick
        self.rw_last_wake = ac.wake

    def _go_around(self, ac: TowerAircraft, provenance: str, commanded: bool = False) -> None:
        ac.phase = "go_around"
        ac.cleared = False
        ac.reentry_at = self.tick + kmrl_twr.GO_AROUND_REENTRY_SEC
        ac.approaches += 1
        self.log.emit(self.tick, E.GO_AROUND, acid=ac.acid, provenance=provenance,
                      commanded=commanded, dist_nm=round(ac.dist_nm, 2))
        self._tx(ac.acid, self.vb.render({"kind": "tower_goaround", "acid": ac.acid,
                                          "persona": "airline_crisp"}))

    def _step_kinematics(self) -> None:
        for acid, ac in list(self.active.items()):
            if ac.role == "arrival":
                self._step_arrival(ac)
            else:
                self._step_departure(ac)
        self._check_runway_conflicts()

    def _step_arrival(self, ac: TowerAircraft) -> None:
        if ac.phase in ("final", "cleared_land"):
            aps = perf(ac.actype).approach_kt / 3600.0
            ac.dist_nm -= aps * SWEEP_SEC
            if ac.acid in self._forced and not ac.forced_done and ac.dist_nm <= 2.0:
                ac.forced_done = True
                self._go_around(ac, provenance="environment")
            elif ac.phase == "final" and ac.dist_nm <= kmrl_twr.GO_AROUND_NM:
                self._go_around(ac, provenance="model")  # never cleared in time
            elif ac.phase == "cleared_land" and ac.dist_nm <= 0:
                if self._runway_free(excluding=ac.acid):
                    ac.phase = "landing"
                    ac.occupy_until = self.tick + kmrl_twr.OCCUPY_LAND_SEC
                    self._begin_use(ac)
                else:
                    self._go_around(ac, provenance="model")  # runway occupied at threshold
        elif ac.phase == "landing":
            if self.tick >= ac.occupy_until:
                ac.phase = "vacated"
                self.log.emit(self.tick, E.LANDED, acid=ac.acid)
                self.strips.strip_delete(ac.acid)
                del self.active[ac.acid]
        elif ac.phase == "go_around":
            if self.tick >= ac.reentry_at:
                ac.phase = "final"
                ac.dist_nm = kmrl_twr.FINAL_START_NM

    def _step_departure(self, ac: TowerAircraft) -> None:
        if ac.phase == "takeoff" and self.tick >= ac.occupy_until:
            ac.phase = "airborne"

    def _check_runway_conflicts(self) -> None:
        occupants = [acid for acid, ac in self.active.items() if ac.on_runway()]
        if len(occupants) >= 2:
            key = frozenset(occupants)
            if key not in self._los_fired:
                self._los_fired.add(key)
                self.log.emit(self.tick, E.LOS_EVENT, acids=sorted(occupants),
                              kind="runway_occupancy", provenance="model")

    # --- driver --------------------------------------------------------------

    def run(self, adapter) -> TowerSessionResult:
        while self.tick <= self.scn.session_seconds:
            self._process_spawns()
            if self.active:
                self._give_model_turns(adapter)
            self._step_kinematics()
            self.tick += SWEEP_SEC
            if len(self._spawned) >= len(self._spawns) and not self.active:
                break
        self.log.emit(self.tick, E.SESSION_END, position=self.scn.position, seed=self.scn.seed)
        vb_cache = self.vb.cache.to_dict() if isinstance(self.vb, CachedVerbalizer) else None
        return TowerSessionResult(
            scenario=self.scn, log=self.log, transcript=self.transcript,
            strips_history=self.strips.history, model_io=self.model_io,
            prompt_hash=self.prompt_hash, verbalizer_cache=vb_cache, regime=self.regime.name)
