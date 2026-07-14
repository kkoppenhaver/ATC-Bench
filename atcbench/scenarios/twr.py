"""Tower scenario generator (DESIGN §6.3, §12).

Arrivals appear on an ~8 NM final and departures at the hold short, spaced so a single
runway is workable by a competent controller (feasibility, §12.2). A seeded
environment-forced go-around (e.g., a blown tire on the runway) may be injected; it is
tagged with environment provenance so it is excluded from *model-caused* go-arounds.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from ..charts import kmrl_twr
from ..domain import ErrorEvent
from ..sim.performance import wake_of
from . import fleet

BANDS = {
    "calm": {"n_arr": 4, "n_dep": 3, "arr_gap": 165, "dep_gap": 190, "forced_ga": 0,
             "error_rate": 0.0},
    "standard": {"n_arr": 6, "n_dep": 5, "arr_gap": 140, "dep_gap": 165, "forced_ga": 1,
                 "error_rate": 0.15},
    "heavy": {"n_arr": 9, "n_dep": 8, "arr_gap": 105, "dep_gap": 120, "forced_ga": 1,
              "error_rate": 0.3},
}

_AIRLINES = ["AAL", "UAL", "SWA", "DAL"]
# Fleet mix: mostly large, with occasional heavy (B77W) and small (C172).
_TYPES = ["B738", "A320", "B739", "E175", "B738", "A320", "B77W", "C172"]


@dataclass
class TowerSpawn:
    acid: str
    actype: str
    wake: str
    role: str            # "arrival" | "departure"
    call_tick: int
    start_dist_nm: float = 0.0   # arrivals only

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TWRScenario:
    seed: int
    band: str
    position: str
    session_seconds: int
    arrivals: list[TowerSpawn]
    departures: list[TowerSpawn]
    forced_go_arounds: list[str] = field(default_factory=list)
    # Per-aircraft injections (§6.3): TWR-SLOW-EXIT (arrivals), TWR-LUAW-MISS (departures).
    error_schedule: dict[str, ErrorEvent] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "seed": self.seed, "band": self.band, "position": self.position,
            "session_seconds": self.session_seconds,
            "arrivals": [a.to_dict() for a in self.arrivals],
            "departures": [d.to_dict() for d in self.departures],
            "forced_go_arounds": list(self.forced_go_arounds),
            "error_schedule": {k: v.to_dict() for k, v in self.error_schedule.items()},
        }

    def all_spawns(self) -> list[TowerSpawn]:
        return sorted(self.arrivals + self.departures, key=lambda s: s.call_tick)




MAX_GEN_ATTEMPTS = 20
_SEED_STRIDE = 1_000_003  # deterministic reroll offset for infeasible candidates


def generate(seed: int, band: str = "standard", session_seconds: int = 3600) -> TWRScenario:
    """Generate a *feasible* scenario (§12.2): candidates the oracle cannot work
    cleanly are rejected and deterministically rerolled. The scenario records the
    seed actually used, so regeneration from the run record is a fixed point."""
    for attempt in range(MAX_GEN_ATTEMPTS):
        scn = _generate_once(seed + attempt * _SEED_STRIDE, band, session_seconds)
        from ..baselines.feasibility import twr_feasible

        if twr_feasible(scn):
            return scn
    raise RuntimeError(  # pragma: no cover - generator defect, not model fault
        f"no feasible TWR scenario within {MAX_GEN_ATTEMPTS} rerolls of seed {seed}")


def _generate_once(seed: int, band: str, session_seconds: int) -> TWRScenario:
    if band not in BANDS:
        raise ValueError(f"unknown band {band!r}")
    cfg = BANDS[band]
    from .seeds import SeedManager

    sm = SeedManager(seed)
    traffic = sm.stream("traffic")
    callsigns = sm.stream("callsigns")
    used: set[str] = set()

    def pick_type() -> str:
        # Keep heavies from clustering: 1-in-8 chance, others large/small.
        return traffic.choice(_TYPES)

    arrivals: list[TowerSpawn] = []
    t = 30
    for _ in range(cfg["n_arr"]):
        actype = pick_type()
        arrivals.append(TowerSpawn(
            acid=fleet.make_callsign(callsigns, actype, _AIRLINES, used),
            actype=actype, wake=wake_of(actype),
            role="arrival", call_tick=t, start_dist_nm=kmrl_twr.FINAL_START_NM))
        t += cfg["arr_gap"] + traffic.randint(-15, 15)

    departures: list[TowerSpawn] = []
    t = 60  # offset so departures interleave with arrivals
    for _ in range(cfg["n_dep"]):
        actype = pick_type()
        departures.append(TowerSpawn(
            acid=fleet.make_callsign(callsigns, actype, _AIRLINES, used),
            actype=actype, wake=wake_of(actype),
            role="departure", call_tick=t))
        t += cfg["dep_gap"] + traffic.randint(-15, 15)

    forced: list[str] = []
    if cfg["forced_ga"] and arrivals:
        # Force a mid-session arrival to go around (environment-caused).
        victim = arrivals[len(arrivals) // 2]
        forced.append(victim.acid)

    # Tower injections (§6.3): slow runway exits and missed LUAW readbacks.
    errors = sm.stream("errors")
    schedule: dict[str, ErrorEvent] = {}
    for sp in arrivals:
        if sp.acid not in forced and errors.random() < cfg["error_rate"]:
            schedule[sp.acid] = ErrorEvent(code="TWR-SLOW-EXIT",
                                           detail={"extra_sec": errors.choice([15, 25, 35])})
    for sp in departures:
        if errors.random() < cfg["error_rate"]:
            schedule[sp.acid] = ErrorEvent(code="TWR-LUAW-MISS", detail={})

    return TWRScenario(
        seed=seed, band=band, position=kmrl_twr.POSITION, session_seconds=session_seconds,
        arrivals=arrivals, departures=departures, forced_go_arounds=forced,
        error_schedule=schedule)
