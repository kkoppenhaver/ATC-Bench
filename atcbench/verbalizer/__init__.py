"""Verbalization layer (DESIGN §8.1).

Converts FSM intent to a single radio-call string. The design pins an LLM verbalizer
at temp 0 with a response cache; for the Phase 1 walking skeleton we ship a
deterministic *template* verbalizer (no API, trivially reproducible). The interface
is the same, so an LLM-backed verbalizer can slot in behind the cache later without
touching the FSM — behavior never depends on wording (principle #4).
"""

from .template import TemplateVerbalizer, spoken_digits, spoken_altitude

__all__ = ["TemplateVerbalizer", "spoken_digits", "spoken_altitude"]
