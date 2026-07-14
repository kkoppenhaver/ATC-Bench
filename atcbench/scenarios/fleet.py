"""Fleet/callsign pairing (P4.0e).

GA airframes fly under N-number registrations; airline callsigns belong to airliner
types only. Registrations follow the real convention: N + 3 digits + 2 letters,
excluding I and O (too easily confused with 1 and 0).
"""

from __future__ import annotations

GA_TYPES = {"C172"}
_REG_LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ"  # no I/O, per FAA registration practice


def make_callsign(rng, actype: str, airlines: list[str], used: set[str]) -> str:
    """A callsign appropriate to the airframe: N-number for GA, airline flight
    number otherwise."""
    for _ in range(1000):
        if actype in GA_TYPES:
            acid = (f"N{rng.randint(100, 999)}"
                    f"{rng.choice(_REG_LETTERS)}{rng.choice(_REG_LETTERS)}")
        else:
            acid = f"{rng.choice(airlines)}{rng.randint(100, 4999)}"
        if acid not in used:
            used.add(acid)
            return acid
    raise RuntimeError("callsign space exhausted")  # pragma: no cover
