"""Tiered phraseology parser (DESIGN §7.2).

Deterministic grammar + normalization. Accepts numbers as spoken words or digits,
"point"/"decimal" equivalently, and grouped or single-digit forms where the FAA
allows. For the CD slice this parses the *controller's* transmissions (the model's
clearances and corrections) into structured CRAFT elements, and matches a
transmission to the aircraft it addresses.

The four parse tiers (§7.2) are represented by :class:`ParseTier`. The parser never
decides pilot behavior — it only extracts structure; the FSM (fsm.py) decides how a
pilot reacts, including how it (mis)hears an ambiguous transmission.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Iterable, Optional

_UNITS = {
    "zero": 0, "oh": 0, "o": 0, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "fife": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "niner": 9,
}
_TEENS = {
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90,
}
_MULT = {"hundred": 100, "thousand": 1000}
_NUMBERISH = set(_UNITS) | set(_TEENS) | set(_TENS) | set(_MULT) | {"point", "decimal"}

# Airline telephony → ICAO 3-letter designators (subset used in the CD slice).
AIRLINE_WORDS = {
    "american": "AAL", "united": "UAL", "southwest": "SWA", "delta": "DAL",
    "jetblue": "JBU", "spirit": "NKS", "frontier": "FFT", "envoy": "ENY",
}


class ParseTier(IntEnum):
    STANDARD = 1
    NONSTANDARD = 2
    AMBIGUOUS = 3
    UNPARSEABLE = 4


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z]+|\d+|\.", text.lower().replace("-", " "))


def _convert_run(tokens: list[str]) -> str:
    """Convert a maximal run of number-ish tokens to a digit/decimal string.

    Runs containing hundred/thousand are read as a cardinal magnitude
    ("five thousand" -> "5000", "one six thousand" -> "16000"); otherwise the run
    is read digit/group-wise ("twenty four fifty two" -> "2452", "one one niner
    point three five" -> "119.35").
    """
    if any(t in _MULT for t in tokens):
        return _convert_magnitude(tokens)
    out: list[str] = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t in ("point", "decimal"):
            out.append(".")
        elif t == ".":
            out.append(".")
        elif t.isdigit():
            out.append(t)
        elif t in _UNITS:
            out.append(str(_UNITS[t]))
        elif t in _TEENS:
            out.append(f"{_TEENS[t]:02d}")
        elif t in _TENS:
            if i + 1 < len(tokens) and tokens[i + 1] in _UNITS:
                out.append(str(_TENS[t] + _UNITS[tokens[i + 1]]))
                i += 1
            else:
                out.append(str(_TENS[t]))
        i += 1
    return "".join(out)


def _convert_magnitude(tokens: list[str]) -> str:
    """Read a run like 'five thousand five hundred' as a cardinal integer string."""
    total = 0
    current_digits = ""
    for t in tokens:
        if t.isdigit():
            current_digits += t
        elif t in _UNITS:
            current_digits += str(_UNITS[t])
        elif t in _TEENS:
            current_digits += str(_TEENS[t])
        elif t in _TENS:
            current_digits += str(_TENS[t])
        elif t in _MULT:
            base = int(current_digits) if current_digits else 1
            total += base * _MULT[t]
            current_digits = ""
    if current_digits:
        total += int(current_digits)
    return str(total)


def normalize(text: str) -> str:
    """Lowercase and convert number words to digits, preserving other words."""
    tokens = _tokenize(text)
    out: list[str] = []
    i = 0
    while i < len(tokens):
        if tokens[i] in _NUMBERISH or tokens[i].isdigit() or tokens[i] == ".":
            j = i
            run: list[str] = []
            while j < len(tokens) and (
                tokens[j] in _NUMBERISH or tokens[j].isdigit() or tokens[j] == "."
            ):
                run.append(tokens[j])
                j += 1
            out.append(_convert_run(run))
            i = j
        else:
            out.append(tokens[i])
            i += 1
    return " ".join(out)


def extract_callsign(text: str, candidates: Iterable[str]) -> Optional[str]:
    """Return the active aircraft the transmission addresses, or None.

    Matching is robust to spoken/written forms: it compares the digit run in the
    transmission against each candidate's numeric part, optionally corroborated by
    the airline word. Given the set of aircraft actually on frequency, this is
    reliable without a full callsign grammar.
    """
    norm = normalize(text)
    digit_runs = re.findall(r"\d+", norm)
    lower = text.lower()
    best: Optional[str] = None
    best_score = 0
    for acid in candidates:
        m = re.match(r"([A-Za-z]+)(\d+)", acid)
        if not m:
            continue
        prefix, number = m.group(1).upper(), m.group(2)
        score = 0
        if number in digit_runs:
            score += 2
        elif any(number in run for run in digit_runs):
            score += 1
        airline = next((w for w, d in AIRLINE_WORDS.items() if d == prefix), None)
        if airline and airline in lower:
            score += 2
        if prefix.lower() in lower or acid.lower() in lower:
            score += 2
        if score > best_score:
            best_score, best = score, acid
    return best if best_score >= 2 else None


def extract_altitude(text: str) -> Optional[int]:
    norm = normalize(text)
    m = re.search(r"(?:climb|maintain|altitude|to)\s+(?:and maintain\s+)?(\d{3,5})", norm)
    if m:
        return int(m.group(1))
    # Fall back to any plausible altitude-magnitude token (multiple of 100, >= 1000).
    for run in re.findall(r"\d+", norm):
        val = int(run)
        if 1000 <= val <= 60000 and val % 100 == 0:
            return val
    return None


def extract_frequency(text: str) -> Optional[str]:
    norm = normalize(text)
    m = re.search(r"(\d{2,3}\.\d{1,3})", norm)
    if m:
        return m.group(1)
    # "one one niner three five" with no explicit point -> 5-6 digit run split 3.2/3.3
    for run in re.findall(r"\d{5,6}", norm):
        if run.startswith("1"):
            return f"{run[:3]}.{run[3:]}"
    return None


def extract_squawk(text: str) -> Optional[str]:
    norm = normalize(text)
    m = re.search(r"squawk\D*(\d{4})", norm)
    if m:
        return m.group(1)
    m = re.search(r"(?:transponder|code)\D*(\d{4})", norm)
    return m.group(1) if m else None


def extract_sid(text: str, sids: dict) -> Optional[str]:
    lower = text.lower()
    for code, sid in sids.items():
        if code.lower() in lower.replace(" ", ""):
            return code
        if sid["name"].lower() in lower:
            return code
    return None


def extract_destination(text: str, destinations: dict) -> Optional[str]:
    lower = text.lower()
    for icao, name in destinations.items():
        if icao.lower() in lower or name.lower() in lower:
            return icao
    return None


_PHONETIC = {
    "alpha": "a", "bravo": "b", "charlie": "c", "delta": "d", "echo": "e",
    "foxtrot": "f", "golf": "g", "hotel": "h",
}
_RWY_SUFFIX = {"left": "l", "right": "r", "center": "c", "centre": "c"}


def _canon_runways(norm: str) -> str:
    """Rewrite spoken runway suffixes so runways appear as '31r'/'31c' tokens."""
    def repl(m):
        return m.group(1) + _RWY_SUFFIX[m.group(2)]

    return re.sub(r"(\d{1,2})\s+(left|right|center|centre)\b", repl, norm)


@dataclass
class ParsedGroundTransmission:
    raw: str
    acid: Optional[str]
    intent: str  # "taxi" | "crossing" | "hold" | "other"
    to_runway: Optional[str] = None
    to_gate: Optional[str] = None
    via: list[str] = field(default_factory=list)
    cross: list[str] = field(default_factory=list)
    hold_short: list[str] = field(default_factory=list)
    hold_position: bool = False


def parse_ground_transmission(text: str, active_acids) -> ParsedGroundTransmission:
    acid = extract_callsign(text, active_acids)
    lower = text.lower()
    norm = _canon_runways(normalize(text))

    cross = re.findall(r"cross\w*\s+runway\s+(\d{1,2}[lrc])", norm)
    hold_short = re.findall(r"hold\s+short\s+(?:of\s+)?runway\s+(\d{1,2}[lrc])", norm)
    hold_position = "hold position" in lower or "hold your position" in lower

    via: list[str] = []
    for m in re.findall(r"via\s+(?:taxiway\s+)?([a-z]+)", lower):
        via.append(_PHONETIC.get(m, m[:1]))

    to_runway = None
    for rwy in re.findall(r"runway\s+(\d{1,2}[lrc])", norm):
        if rwy not in hold_short and rwy not in cross:
            to_runway = rwy
            break
    to_gate = None
    mg = re.search(r"\b(g\d)\b|gate\s+(g?\d)", lower)
    if mg:
        to_gate = (mg.group(1) or ("g" + mg.group(2).lstrip("g"))).upper()

    if cross:
        intent = "crossing"
    elif to_runway or via or to_gate:
        intent = "taxi"
    elif hold_position:
        intent = "hold"
    else:
        intent = "other"

    return ParsedGroundTransmission(
        raw=text, acid=acid, intent=intent,
        to_runway=to_runway.upper() if to_runway else None,
        to_gate=to_gate, via=via,
        cross=[c.upper() for c in cross], hold_short=[h.upper() for h in hold_short],
        hold_position=hold_position,
    )


@dataclass
class ParsedTransmission:
    """Structured result of parsing one controller transmission."""

    raw: str
    acid: Optional[str]
    tier: ParseTier
    intent: str  # "clearance" | "correction" | "affirm" | "say_again" | "other"
    altitude: Optional[int] = None
    frequency: Optional[str] = None
    squawk: Optional[str] = None
    sid: Optional[str] = None
    destination: Optional[str] = None
    extras: dict = field(default_factory=dict)


def parse_controller_transmission(
    text: str, active_acids: Iterable[str], pack
) -> ParsedTransmission:
    """Parse the model's transmission for the CD position.

    ``pack`` is a chart pack exposing ``SIDS``/``KNOWN_DESTINATIONS`` via the module
    (charts.kmdw_cd). Determines the addressed aircraft, the intent, and any CRAFT
    elements present, and assigns a parse tier.
    """
    from ..charts import kmdw_cd

    acid = extract_callsign(text, active_acids)
    lower = text.lower()

    altitude = extract_altitude(text)
    frequency = extract_frequency(text)
    squawk = extract_squawk(text)
    sid = extract_sid(text, kmdw_cd.SIDS)
    destination = extract_destination(text, kmdw_cd.KNOWN_DESTINATIONS)

    is_clearance = ("cleared to" in lower) or (altitude and squawk)
    is_correction = any(
        w in lower for w in ("negative", "correction", "i say again", "readback", "verify", "disregard")
    )
    is_say_again = "say again" in lower and not is_correction
    is_affirm = any(w in lower for w in ("readback correct", "correct,", "affirmative")) and not (
        altitude or squawk
    )

    if is_correction:
        intent = "correction"
    elif is_clearance:
        intent = "clearance"
    elif is_say_again:
        intent = "say_again"
    elif is_affirm:
        intent = "affirm"
    else:
        intent = "other"

    # Tier assignment (§7.2): a clearance missing its callsign is ambiguous; a
    # clearance missing safety-critical numeric elements is nonstandard/ambiguous.
    if intent == "clearance":
        if acid is None:
            tier = ParseTier.AMBIGUOUS
        elif altitude and squawk and frequency:
            tier = ParseTier.STANDARD
        elif altitude or squawk:
            tier = ParseTier.NONSTANDARD
        else:
            tier = ParseTier.AMBIGUOUS
    elif intent in ("correction", "affirm", "say_again"):
        tier = ParseTier.AMBIGUOUS if acid is None else ParseTier.STANDARD
    else:
        tier = ParseTier.UNPARSEABLE if acid is None else ParseTier.NONSTANDARD

    return ParsedTransmission(
        raw=text,
        acid=acid,
        tier=tier,
        intent=intent,
        altitude=altitude,
        frequency=frequency,
        squawk=squawk,
        sid=sid,
        destination=destination,
    )
