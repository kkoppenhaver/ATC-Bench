"""Time regimes (DESIGN §4.2).

Both regimes share the same sim; only the accounting for the model's *thinking* differs.

- **Turn-based** — the sim pauses while the model reasons; thinking is free. (Transmissions
  still consume sim time via frequency physics, §7.1 — that is handled by the session, not
  here.) Measures pure decision quality.
- **Token-metered** — the model's output consumes sim time at ``R`` tokens/sim-second while
  the world keeps moving: ``sim_seconds = ceil(output_tokens / R)``. Measures operational
  tempo — a verbose model literally falls behind the traffic.

``R`` is a pinned benchmark constant (``TOKENS_PER_SIM_SEC``); changing it is a major
version bump. Both regimes are reported as separate leaderboard columns; the delta is the
``tempo_gap``.
"""

from __future__ import annotations

import math

from .. import TOKENS_PER_SIM_SEC


class TimeRegime:
    name = "turn"

    def thinking_seconds(self, output_tokens: int) -> int:
        return 0


class TurnBased(TimeRegime):
    name = "turn"


class TokenMetered(TimeRegime):
    name = "metered"

    def __init__(self, tokens_per_sim_sec: int = TOKENS_PER_SIM_SEC):
        self.R = tokens_per_sim_sec

    def thinking_seconds(self, output_tokens: int) -> int:
        return math.ceil(max(0, int(output_tokens)) / self.R)


def make_regime(name: str) -> TimeRegime:
    if name in ("metered", "token", "token-metered"):
        return TokenMetered()
    if name == "turn":
        return TurnBased()
    raise ValueError(f"unknown regime {name!r}; choose 'turn' or 'metered'")
