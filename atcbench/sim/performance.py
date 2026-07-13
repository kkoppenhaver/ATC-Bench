"""Aircraft performance table (DESIGN §3.2, cross-cutting task X.1).

A static, pinned per-type table: approach speed, wake category, and (for later
airborne positions) climb rate. OpenAP can seed richer values later; this ships
pinned so trajectories are reproducible across versions. Wake categories drive the
Tower wake-separation matrix (§4.5).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Performance:
    actype: str
    wake: str          # "S" small, "L" large, "H" heavy
    approach_kt: int   # final approach IAS
    climb_fpm: int     # initial climb rate (used from TRACON/Center on)


_TABLE: dict[str, Performance] = {
    "B738": Performance("B738", "L", 135, 2500),
    "A320": Performance("A320", "L", 135, 2400),
    "B739": Performance("B739", "L", 140, 2400),
    "E175": Performance("E175", "L", 130, 2600),
    "C172": Performance("C172", "S", 70, 700),
    "B77W": Performance("B77W", "H", 145, 2000),
}

_DEFAULT = Performance("B738", "L", 135, 2500)


def perf(actype: str) -> Performance:
    return _TABLE.get(actype, _DEFAULT)


def wake_of(actype: str) -> str:
    return perf(actype).wake


# Minimum seconds between successive runway uses, indexed [leader_wake][follower_wake]
# (a simplified single-runway rendering of the wake matrix, §4.5). Real spacing mixes
# time and distance; this benchmark-normalized table is pinned.
WAKE_MIN_SEC: dict[str, dict[str, int]] = {
    "H": {"S": 180, "L": 120, "H": 90},
    "L": {"S": 120, "L": 60, "H": 60},
    "S": {"S": 60, "L": 60, "H": 60},
}


def wake_min_sec(leader_wake: str, follower_wake: str) -> int:
    return WAKE_MIN_SEC.get(leader_wake, WAKE_MIN_SEC["L"]).get(follower_wake, 60)
