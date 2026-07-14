"""Shared half-duplex frequency channel (DESIGN §7.1; audit M4, P4.0a).

One channel per session, used by every speaker — the controller under test, pilots,
and scripted Tower coordination. Broadcast duration is ``ceil(words / 2.5)`` (150 wpm).
Two usage modes:

- **Queueing** (``transmit``): the transmission starts when the channel frees up and
  extends the busy window. Used by pilots/coordination (they wait their turn), and by
  the CD session, whose event-driven clock then jumps to the end of the broadcast.
- **Blocking** (``is_busy`` checked by the time-stepped sessions): a controller keying
  up while the channel is busy is stepped on — the transmission does not happen, a
  ``blocked_transmission`` event is logged, and the action window for the sweep is
  forfeited. This is what makes verbosity and correction spam an operational cost at
  GND/TWR (principle #3).
"""

from __future__ import annotations

import math

from .. import WORDS_PER_SECOND
from ..pilots import parser as P


def broadcast_seconds(text: str) -> int:
    words = len(P.normalize(text).split())
    return max(1, math.ceil(words / WORDS_PER_SECOND))


class FrequencyChannel:
    def __init__(self) -> None:
        self.busy_until = 0

    def is_busy(self, tick: int) -> bool:
        return self.busy_until > tick

    def transmit(self, tick: int, text: str) -> tuple[int, int]:
        """Queue a broadcast after any transmission in progress; returns (start, end)."""
        start = max(tick, self.busy_until)
        end = start + broadcast_seconds(text)
        self.busy_until = end
        return start, end
