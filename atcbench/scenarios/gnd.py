"""Ground scenario generator (DESIGN §6.2, §12).

Produces departures (calling for taxi from gates) and arrivals (handed off from Tower
at the runway exit), a Tower-requested departure sequence the controller should honor,
and a scripted schedule of times when the crossing runway (31R) is hot. All seeded and
reproducible.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from ..charts import kmrl_gnd
from ..domain import ErrorEvent
from . import fleet
from .seeds import SeedManager

BANDS = {
    "calm": {"n_dep": 3, "n_arr": 2, "hot_period": 220, "error_rate": 0.0},
    "standard": {"n_dep": 5, "n_arr": 3, "hot_period": 170, "error_rate": 0.15},
    "heavy": {"n_dep": 8, "n_arr": 5, "hot_period": 130, "error_rate": 0.30},
}

HOT_DURATION = 25  # sim-seconds a 31R hot window lasts (arrival rollout / departure roll)
_AIRLINES = ["AAL", "UAL", "SWA", "DAL"]
_TYPES = [("B738", "L"), ("A320", "L"), ("B739", "L"), ("E175", "L"), ("C172", "S")]


@dataclass
class GroundSpawn:
    acid: str
    actype: str
    wake: str
    role: str
    gate: str
    node: str  # spawn node
    goal: str  # goal node
    call_tick: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class GNDScenario:
    seed: int
    band: str
    position: str
    session_seconds: int
    departures: list[GroundSpawn]
    arrivals: list[GroundSpawn]
    tower_sequence: list[str]
    hot_windows: dict[str, list[list[int]]]
    error_schedule: dict[str, ErrorEvent] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "seed": self.seed,
            "band": self.band,
            "position": self.position,
            "session_seconds": self.session_seconds,
            "departures": [d.to_dict() for d in self.departures],
            "arrivals": [a.to_dict() for a in self.arrivals],
            "tower_sequence": list(self.tower_sequence),
            "hot_windows": self.hot_windows,
            "error_schedule": {k: v.to_dict() for k, v in self.error_schedule.items()},
        }

    def all_spawns(self) -> list[GroundSpawn]:
        return sorted(self.departures + self.arrivals, key=lambda s: s.call_tick)

    def is_hot(self, runway: str, tick: int) -> bool:
        return any(s <= tick < e for s, e in self.hot_windows.get(runway, []))

    def next_hot_after(self, runway: str, tick: int) -> int | None:
        upcoming = [s for s, _ in self.hot_windows.get(runway, []) if s >= tick]
        return min(upcoming) if upcoming else None




MAX_GEN_ATTEMPTS = 20
_SEED_STRIDE = 1_000_003  # deterministic reroll offset for infeasible candidates


def generate(seed: int, band: str = "standard", session_seconds: int = 3600) -> GNDScenario:
    """Generate a *feasible* scenario (§12.2): candidates the oracle cannot work
    cleanly are rejected and deterministically rerolled. The scenario records the
    seed actually used, so regeneration from the run record is a fixed point."""
    for attempt in range(MAX_GEN_ATTEMPTS):
        scn = _generate_once(seed + attempt * _SEED_STRIDE, band, session_seconds)
        from ..baselines.feasibility import gnd_feasible

        if gnd_feasible(scn):
            return scn
    raise RuntimeError(  # pragma: no cover - generator defect, not model fault
        f"no feasible GND scenario within {MAX_GEN_ATTEMPTS} rerolls of seed {seed}")


def _generate_once(seed: int, band: str, session_seconds: int) -> GNDScenario:
    if band not in BANDS:
        raise ValueError(f"unknown band {band!r}")
    cfg = BANDS[band]
    sm = SeedManager(seed)
    traffic = sm.stream("traffic")
    errors = sm.stream("errors")
    callsigns = sm.stream("callsigns")
    coord = sm.stream("coordination")

    used: set[str] = set()
    departures: list[GroundSpawn] = []
    for _ in range(cfg["n_dep"]):
        actype, wake = traffic.choice(_TYPES)
        gate = traffic.choice(kmrl_gnd.GATES)
        departures.append(GroundSpawn(
            acid=fleet.make_callsign(callsigns, actype, _AIRLINES, used),
            actype=actype, wake=wake, role="departure",
            gate=gate, node=gate, goal="HS_31C",
            call_tick=traffic.randint(20, max(40, session_seconds - 400)),
        ))
    arrivals: list[GroundSpawn] = []
    for _ in range(cfg["n_arr"]):
        actype, wake = traffic.choice(_TYPES)
        gate = traffic.choice(kmrl_gnd.GATES)
        arrivals.append(GroundSpawn(
            acid=fleet.make_callsign(callsigns, actype, _AIRLINES, used),
            actype=actype, wake=wake, role="arrival",
            gate=gate, node="RWEX", goal=gate,
            call_tick=traffic.randint(20, max(40, session_seconds - 400)),
        ))

    departures.sort(key=lambda d: d.call_tick)
    tower_sequence = [d.acid for d in departures]

    # Scripted 31R hot windows, spaced so a crossing fits comfortably in the gaps.
    hot: list[list[int]] = []
    t = cfg["hot_period"]
    while t < session_seconds - HOT_DURATION:
        jitter = coord.randint(-15, 15)
        start = max(0, t + jitter)
        hot.append([start, start + HOT_DURATION])
        t += cfg["hot_period"]

    # Ground error schedule: departures may drop the hold-short readback; arrivals
    # may take a wrong turn (fly A when cleared via B) — both pilot-provenance.
    schedule: dict[str, ErrorEvent] = {}
    for sp in departures:
        if errors.random() < cfg["error_rate"]:
            schedule[sp.acid] = ErrorEvent(code="GND-HS-DROP", detail={})
    for sp in arrivals:
        if errors.random() < cfg["error_rate"]:
            schedule[sp.acid] = ErrorEvent(code="GND-WRONG-TURN", detail={})

    return GNDScenario(
        seed=seed, band=band, position=kmrl_gnd.POSITION, session_seconds=session_seconds,
        departures=departures, arrivals=arrivals, tower_sequence=tower_sequence,
        hot_windows={"31R": hot}, error_schedule=schedule,
    )
