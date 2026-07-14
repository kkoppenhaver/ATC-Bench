"""Named-PRNG seed manager (DESIGN §12.1).

All randomness in ATCBench flows from a single master seed through independent,
named streams. Independent streams mean difficulty dials can move without
reshuffling everything else, and two runs with the same master seed produce
identical worlds (principle #2: reproducibility over realism).

A stream's sequence depends only on (master_seed, stream_name), never on the
order in which streams are drawn — each name is hashed to its own sub-seed.
"""

from __future__ import annotations

import hashlib
import random

# The canonical named streams (DESIGN §12.1). `airspace` is Generalist-track only.
STREAM_NAMES = (
    "traffic",
    "errors",
    "weather",
    "callsigns",
    "airspace",
    "coordination",
)


def _derive_seed(master_seed: int, stream_name: str) -> int:
    """Deterministically derive a 64-bit sub-seed for a named stream."""
    payload = f"{master_seed}:{stream_name}".encode()
    digest = hashlib.sha256(payload).digest()
    return int.from_bytes(digest[:8], "big")


class SeedManager:
    """Hands out one independent ``random.Random`` per named stream.

    Uses the stdlib Mersenne Twister. The core generator is stable across CPython
    versions; derived methods (``choice``/``randint``/...) are stable in practice but
    not formally guaranteed, so the CI ``cross-version-determinism`` job byte-compares
    run artifacts across the supported interpreters (§17.2), and every run record
    carries its ``python_version``.
    """

    def __init__(self, master_seed: int):
        self.master_seed = int(master_seed)
        self._streams: dict[str, random.Random] = {}

    def stream(self, name: str) -> random.Random:
        """Return the (cached) PRNG for ``name``, creating it on first use."""
        if name not in self._streams:
            self._streams[name] = random.Random(_derive_seed(self.master_seed, name))
        return self._streams[name]

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"SeedManager(master_seed={self.master_seed})"
