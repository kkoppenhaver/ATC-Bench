"""System-prompt assembly (DESIGN §11.3).

Assembles the versioned, per-position system prompt from the chart pack. Its hash is
part of the run record (§11.4). Everything the model needs — the chart pack, the
protocol rules, the tool surface, the scoring-relevant obligations — is stated here;
nothing in the prompt leaks per-scenario ground truth (§11.2).
"""

from __future__ import annotations

import hashlib

from ..charts import kmrl_cd, kmrl_gnd, kmrl_twr
from ..sim.performance import _TABLE, WAKE_MIN_SEC

PROMPT_TEMPLATE_VERSIONS = {"CD": "cd-v5", "GND": "gnd-v3", "TWR": "twr-v3"}

_COMMON_TOOLS = (
    "TOOLS: use `transmit` to speak on frequency (one transmission per call, standard "
    "phraseology, always address a callsign). Use the strip tools "
    "(`strip_create`/`strip_update`/`strip_move`/`strip_delete`) to externalize memory "
    "and `bay_read` to read your bays back — the frequency feed only shows *new* "
    "messages each turn, so your strips are how you keep the picture. Use `wait` to "
    "yield until the next event. Unusable or garbled transmissions get a pilot "
    "\"say again\" and waste time. Pilots you leave waiting will re-call periodically "
    "— a re-call means you owe that aircraft service, and continuing to ignore it "
    "still counts as neglect."
)

def _cd_phraseology(pack) -> str:
    """Phraseology examples rendered from this scenario's own chart pack (§11.3) —
    the examples teach the format, and the pack supplies the local numbers."""
    from ..verbalizer.template import spoken_altitude, spoken_digits

    code = pack.fallback_sid
    sid = pack.sids[code]
    alt = sid["initial_altitude"]
    freq = pack.departure_frequency
    return f"""\
Canonical clearance (CRAFT order), using this facility's chart values:
  "American 2452, cleared to Detroit, {sid['name']} departure, maintain {spoken_altitude(alt)},
   departure {spoken_digits(freq)}, squawk four three two one."
Catching a bad readback:
  pilot: "... maintain {spoken_altitude(alt + 1000)} ..."   controller: "American 2452, negative, maintain {spoken_altitude(alt)}."
Affirming a correct readback (optional): "American 2452, readback correct."
LOCAL READBACK CONVENTION: pilots here read back altitude, departure frequency, and
squawk only — the clearance limit and departure procedure are NOT read back, and
that is a complete readback at this facility. Do not prompt pilots to read back the
departure procedure; they will not respond to such requests.
"""

_GND_PHRASEOLOGY = """\
Departure taxi (route + explicit hold short — required):
  "Southwest 254, runway three one center, taxi via alpha, hold short runway three one right."
Runway crossing (explicit, never implied):
  "Southwest 254, cross runway three one right."
Arrival taxi to the gate:
  "Southwest 254, taxi to the gate via bravo."
Hold in place: "Southwest 254, hold position."
"""

_TWR_PHRASEOLOGY = """\
  "American 2452, runway three one center, cleared to land."
  "American 2452, runway three one center, line up and wait."
  "American 2452, runway three one center, cleared for takeoff."
  "American 2452, go around, I say again, go around."
Departure handoff (once airborne): "American 2452, contact departure one one niner point three five."
"""


def _wake_table_text() -> str:
    lines = ["Minimum seconds between successive runway uses (leader -> follower):"]
    for leader, row in sorted(WAKE_MIN_SEC.items()):
        lines.append("  " + "  ".join(f"{leader}->{f}: {s}s" for f, s in sorted(row.items())))
    lines.append("Aircraft wake categories: "
                 + ", ".join(f"{p.actype}={p.wake}" for p in _TABLE.values())
                 + " (S small, L large, H heavy); approach speeds: "
                 + ", ".join(f"{p.actype} {p.approach_kt}kt" for p in _TABLE.values()) + ".")
    return "\n".join(lines)


def build_cd_system_prompt(session_seconds: int, regime: str = "turn", pack=None) -> str:
    pack = pack or kmrl_cd.PACK
    sections = [
        "1. ROLE: You are the Clearance Delivery controller at Marlow Regional (MRL_CD). "
        "Issue IFR clearances in CRAFT order, verify filed plans against the chart pack "
        "(fix invalid SIDs and LOA-violating filed altitudes — the correct initial "
        "altitude is always the assigned SID's LOA entry), and catch readback errors. "
        "Listen: pilots misspeak altitudes, frequencies, and callsigns, drop readbacks, "
        "or omit elements — correct the erroneous element before the aircraft leaves "
        "your frequency. Correcting a *correct* readback is a false alarm and counts "
        "against you.",
        "2-4. AIRSPACE / PROCEDURES / LOA (this facility's current chart pack — "
        "authoritative for this session):\n" + pack.describe(),
        "5. SEPARATION: not applicable at CD (no aircraft movement).",
        "6. HANDOFF PROTOCOL: not applicable at CD.",
        "7. " + _COMMON_TOOLS,
        "8. PHRASEOLOGY:\n" + _cd_phraseology(pack),
        f"9. SESSION: length {session_seconds} sim-seconds; time regime = {regime}. "
        "Transmissions consume sim time at 150 wpm on a half-duplex channel.",
    ]
    return "\n\n".join(sections)


def build_gnd_system_prompt(session_seconds: int, regime: str = "turn") -> str:
    sections = [
        "1. ROLE: You are the Ground controller at Marlow Regional (MRL_GND). Taxi "
        "departures to the runway and arrivals to the gate. Every taxi clearance must "
        "state a route (via taxiway) — pilots will not move on a bare \"taxi\" and "
        "will ask you to say again the route. Runway crossings must be explicit; "
        "nothing crosses a runway without your crossing clearance.",
        "2-4. SURFACE / PROCEDURES:\n" + kmrl_gnd.describe(),
        "5. RUNWAY SAFETY: Tower owns the runways. Tower announces upcoming departure "
        "traffic on your frequency (\"hold all crossings\") and releases them "
        "(\"crossings at your discretion\") — track this yourself; the observation "
        "shows only whether a runway is hot *right now*. A model-caused runway "
        "incursion or a head-on taxi deadlock ends the session evaluation.",
        "6. COORDINATION: the tower_sequence field lists Tower's requested departure "
        "order at the runway — sequence your departures to match.",
        "7. " + _COMMON_TOOLS,
        "8. PHRASEOLOGY:\n" + _GND_PHRASEOLOGY,
        f"9. SESSION: length {session_seconds} sim-seconds; time regime = {regime}. "
        "Ground radar sweeps every 5 sim-seconds; aircraft keep taxiing while you "
        "think. The frequency is half-duplex at 150 wpm: transmitting while the "
        "channel is busy (a pilot readback, a coordination call, your own previous "
        "transmission still going out) gets you [BLOCKED] and costs your action for "
        "the sweep — keep transmissions short and don't double-key.",
    ]
    return "\n\n".join(sections)


def build_twr_system_prompt(session_seconds: int, regime: str = "turn") -> str:
    sections = [
        "1. ROLE: You are the Tower (Local) controller at Marlow Regional (MRL_TWR). "
        "You own runway 31C: sequence arrivals and departures, one runway use at a "
        "time. An arrival with no landing clearance by one mile goes around (charged "
        "to you); simultaneous runway occupancy or a wake-separation bust ends the "
        "session evaluation. Aircraft you never clear at all count as neglected.",
        "2-4. AIRSPACE / PROCEDURES:\n" + kmrl_twr.describe(),
        "5. SEPARATION:\n" + _wake_table_text() + "\n"
        "Maintain your own runway picture: the observation shows only who occupies "
        "the runway *right now* — track when each use started and its wake category "
        "yourself (your strips help).",
        "6. HANDOFF PROTOCOL: hand airborne departures to Departure "
        f"({kmrl_twr.DEPARTURE_FREQUENCY}) promptly.",
        "7. " + _COMMON_TOOLS,
        "8. PHRASEOLOGY:\n" + _TWR_PHRASEOLOGY,
        f"9. SESSION: length {session_seconds} sim-seconds; time regime = {regime}. "
        "Radar sweeps every 5 sim-seconds; finals keep closing while you think. The "
        "frequency is half-duplex at 150 wpm: transmitting while the channel is busy "
        "gets you [BLOCKED] and costs your action for the sweep — keep transmissions "
        "short and don't double-key.",
    ]
    return "\n\n".join(sections)


_BUILDERS = {
    "CD": build_cd_system_prompt,
    "GND": build_gnd_system_prompt,
    "TWR": build_twr_system_prompt,
}


def build_system_prompt(position: str, session_seconds: int, regime: str = "turn",
                        pack=None) -> tuple[str, str]:
    """Return ``(prompt_text, prompt_hash)`` for a position key ("CD"|"GND"|"TWR").

    CD prompts embed the scenario's seed-drawn chart pack (audit M6), so the hash
    varies per seed by design — the template version prefix identifies the template."""
    if position == "CD":
        text = build_cd_system_prompt(session_seconds, regime, pack)
    else:
        text = _BUILDERS[position](session_seconds, regime)
    return text, prompt_hash(text, PROMPT_TEMPLATE_VERSIONS[position])


def prompt_hash(text: str, version: str = PROMPT_TEMPLATE_VERSIONS["CD"]) -> str:
    return f"{version}:{hashlib.sha256(text.encode()).hexdigest()[:12]}"
