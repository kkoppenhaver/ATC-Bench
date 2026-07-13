"""Deterministic template verbalizer (DESIGN §8.1, §8.3 personas).

Renders FSM intent JSON to one radio string. Persona flags tune tone/verbosity but
never change the underlying intent — even if wording drifts, the aircraft flies the
FSM intent (principle #4).
"""

from __future__ import annotations

from ..domain import Persona

_DIGIT_WORDS = {
    "0": "zero", "1": "one", "2": "two", "3": "three", "4": "four",
    "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "niner",
}


def spoken_digits(s: str) -> str:
    """Render a digit/'.' string as spoken words: '119.35' -> 'one one niner point three five'."""
    out = []
    for ch in str(s):
        if ch == ".":
            out.append("point")
        elif ch in _DIGIT_WORDS:
            out.append(_DIGIT_WORDS[ch])
    return " ".join(out)


def spoken_altitude(feet: int) -> str:
    """Render an altitude as controllers speak it: 5000 -> 'five thousand'."""
    if feet % 1000 == 0:
        thousands = feet // 1000
        return f"{spoken_digits(str(thousands))} thousand"
    thousands = feet // 1000
    hundreds = (feet % 1000) // 100
    return f"{spoken_digits(str(thousands))} thousand {spoken_digits(str(hundreds))} hundred"


def _callsign_words(acid: str) -> str:
    """Best-effort telephony for a callsign, e.g. 'AAL2452' -> 'American 2452'."""
    import re

    from ..pilots.parser import AIRLINE_WORDS

    m = re.match(r"([A-Za-z]+)(\d+)", acid)
    if not m:
        return acid
    prefix, number = m.group(1).upper(), m.group(2)
    airline = next((w for w, d in AIRLINE_WORDS.items() if d == prefix), None)
    spoken_num = spoken_digits(number)
    if airline:
        return f"{airline.capitalize()} {spoken_num}"
    # GA/tail number: "N714KC" -> "November seven one four kilo charlie" (simplified)
    return f"{acid} ({spoken_num})"


class TemplateVerbalizer:
    """Stateless renderer. ``render(intent)`` returns one radio-call string."""

    def render(self, intent: dict) -> str:
        kind = intent["kind"]
        persona = Persona(intent.get("persona", Persona.AIRLINE_CRISP.value))
        method = getattr(self, f"_render_{kind}", None)
        if method is None:  # pragma: no cover - defensive
            return intent.get("acid", "")
        return method(intent, persona)

    # --- CD-position intents -------------------------------------------------

    def _render_check_in(self, intent: dict, persona: Persona) -> str:
        acid = _callsign_words(intent["acid"])
        dest = intent.get("destination_name", "our destination")
        if persona == Persona.STUDENT_PILOT:
            return f"Uh, Midway Clearance, {acid}, we're, uh, ready to copy IFR to {dest}"
        if persona == Persona.FOREIGN_CARRIER:
            return f"Midway Clearance, {acid}, request IFR clearance to {dest}"
        return f"Midway Clearance, {acid}, IFR to {dest}, ready to copy"

    def _render_readback(self, intent: dict, persona: Persona) -> str:
        """Read back the safety-critical numeric elements the FSM decided to voice.

        ``readback`` holds what the pilot says (may contain a scheduled error);
        the fields are already the (possibly wrong) values.
        """
        rb = intent["readback"]
        acid = _callsign_words(intent["acid"])
        parts = []
        if rb.get("altitude") is not None:
            parts.append(f"maintain {spoken_altitude(rb['altitude'])}")
        if rb.get("frequency") is not None:
            parts.append(f"departure {spoken_digits(rb['frequency'])}")
        if rb.get("squawk") is not None:
            parts.append(f"squawk {spoken_digits(rb['squawk'])}")
        body = ", ".join(parts)
        if persona == Persona.STUDENT_PILOT:
            return f"Okay, uh, {body}, {acid}"
        return f"{body}, {acid}"

    def _render_say_again(self, intent: dict, persona: Persona) -> str:
        acid = _callsign_words(intent["acid"])
        return f"Say again for {acid}?"

    def _render_correction_ack(self, intent: dict, persona: Persona) -> str:
        rb = intent["readback"]
        acid = _callsign_words(intent["acid"])
        parts = []
        if rb.get("altitude") is not None:
            parts.append(f"maintain {spoken_altitude(rb['altitude'])}")
        if rb.get("frequency") is not None:
            parts.append(f"departure {spoken_digits(rb['frequency'])}")
        if rb.get("squawk") is not None:
            parts.append(f"squawk {spoken_digits(rb['squawk'])}")
        return f"Correction, {', '.join(parts)}, {acid}"
