"""System-prompt assembly (DESIGN §11.3).

Assembles the versioned, per-position system prompt from the chart pack. Its hash is
part of the run record (§11.4). For the CD slice this covers sections 1-9 at the
detail CD needs; upper positions extend it.
"""

from __future__ import annotations

import hashlib

from ..charts import kmdw_cd

PROMPT_TEMPLATE_VERSION = "cd-v1"

_PHRASEOLOGY_EXAMPLES = """\
Canonical clearance (CRAFT order):
  "American 2452, cleared to Detroit, Midway Seven departure, maintain five thousand,
   departure one one niner point three five, squawk four three two one."
Catching a bad readback:
  pilot: "... maintain six thousand ..."   controller: "American 2452, negative, maintain five thousand."
"""


def build_cd_system_prompt(session_seconds: int, regime: str = "turn") -> str:
    sections = [
        "1. ROLE: You are the Clearance Delivery controller at Chicago Midway (MDW_CD). "
        "Issue IFR clearances in CRAFT order and catch filing and readback errors.",
        "2-4. AIRSPACE / PROCEDURES / LOA:\n" + kmdw_cd.describe(),
        "5. SEPARATION: not applicable at CD (no aircraft movement).",
        "6. HANDOFF PROTOCOL: not applicable at CD.",
        "7. TOOLS: use `transmit` to speak on frequency (one transmission per call). "
        "Use the strip tools to externalize memory. Use `wait` when no action is needed.",
        "8. PHRASEOLOGY:\n" + _PHRASEOLOGY_EXAMPLES,
        f"9. SESSION: length {session_seconds} sim-seconds; time regime = {regime}.",
    ]
    return "\n\n".join(sections)


def prompt_hash(text: str) -> str:
    return f"{PROMPT_TEMPLATE_VERSION}:{hashlib.sha256(text.encode()).hexdigest()[:12]}"
