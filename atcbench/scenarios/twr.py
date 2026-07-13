"""Tower scenario generator (DESIGN §6.3, §12).

Arrivals appear on an ~8 NM final and departures at the hold short, spaced so a single
runway is workable by a competent controller (feasibility, §12.2). A seeded
environment-forced go-around (e.g., a blown tire on the runway) may be injected; it is
tagged with environment provenance so it is excluded from *model-caused* go-arounds.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from ..charts import kmrl_twr
from ..sim.performance import wake_of

BANDS = {
    "calm": {"n_arr": 4, "n_dep": 3, "arr_gap": 165, "dep_gap": 190, "forced_ga": 0},
    "standard": {"n_arr": 6, "n_dep": 5, "arr_gap": 140, "dep_gap": 165, "forced_ga": 1},
    "heavy": {"n_arr": 9, "n_dep": 8, "arr_gap": 105, "dep_gap": 120, "forced_ga": 1},
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

    def to_dict(self) -> dict:
        return {
            "seed": self.seed, "band": self.band, "position": self.position,
            "session_seconds": self.session_seconds,
            "arrivals": [a.to_dict() for a in self.arrivals],
            "departures": [d.to_dict() for d in self.departures],
            "forced_go_arounds": list(self.forced_go_arounds),
        }

    def all_spawns(self) -> list[TowerSpawn]:
        return sorted(self.arrivals + self.departures, key=lambda s: s.call_tick)


def _mk_callsign(rng, used: set[str]) -> str:
    for _ in range(1000):
        acid = f"{rng.choice(_AIRLINES)}{rng.randint(100, 4999)}"
        if acid not in used:
            used.add(acid)
            return acid
    raise RuntimeError("callsign space exhausted")  # pragma: no cover


def generate(seed: int, band: str = "standard", session_seconds: int = 3600) -> TWRScenario:
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
            acid=_mk_callsign(callsigns, used), actype=actype, wake=wake_of(actype),
            role="arrival", call_tick=t, start_dist_nm=kmrl_twr.FINAL_START_NM))
        t += cfg["arr_gap"] + traffic.randint(-15, 15)

    departures: list[TowerSpawn] = []
    t = 60  # offset so departures interleave with arrivals
    for _ in range(cfg["n_dep"]):
        actype = pick_type()
        departures.append(TowerSpawn(
            acid=_mk_callsign(callsigns, used), actype=actype, wake=wake_of(actype),
            role="departure", call_tick=t))
        t += cfg["dep_gap"] + traffic.randint(-15, 15)

    forced: list[str] = []
    if cfg["forced_ga"] and arrivals:
        # Force a mid-session arrival to go around (environment-caused).
        victim = arrivals[len(arrivals) // 2]
        forced.append(victim.acid)

    return TWRScenario(
        seed=seed, band=band, position=kmrl_twr.POSITION, session_seconds=session_seconds,
        arrivals=arrivals, departures=departures, forced_go_arounds=forced)
