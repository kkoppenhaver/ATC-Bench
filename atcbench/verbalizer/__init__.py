"""Verbalization layer (DESIGN §8.1).

Converts FSM intent to a single radio-call string. The design pins an LLM verbalizer
at temp 0 with a response cache; for the Phase 1 walking skeleton we ship a
deterministic *template* verbalizer (no API, trivially reproducible). The interface
is the same, so an LLM-backed verbalizer can slot in behind the cache later without
touching the FSM — behavior never depends on wording (principle #4).
"""

from .cache import CachedVerbalizer, ResponseCache, cache_key
from .template import TemplateVerbalizer, spoken_altitude, spoken_digits


def default_verbalizer(cache: ResponseCache | None = None) -> CachedVerbalizer:
    """The shipping default: cache-first over the deterministic template backend."""
    return CachedVerbalizer(TemplateVerbalizer(), cache)


__all__ = [
    "TemplateVerbalizer",
    "CachedVerbalizer",
    "ResponseCache",
    "cache_key",
    "default_verbalizer",
    "spoken_digits",
    "spoken_altitude",
]
