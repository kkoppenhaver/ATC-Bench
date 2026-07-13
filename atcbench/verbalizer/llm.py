"""Pinned-LLM verbalizer backend (DESIGN §8.1, open question §16.4).

Renders FSM intent -> one radio string via a version-pinned model at temp 0. This is
a *backend* for ``CachedVerbalizer``: run it once per epoch to populate the response
cache, then ship the cache so evaluation runs never hit the API (principle #2). The
model/version/prompt are pinned; changing any of them is a benchmark change and bumps
``VERBALIZER_PROMPT_VERSION``.

Requires the ``anthropic`` extra and an API key; the default deterministic backend is
``TemplateVerbalizer`` (no API), which is what ships until an epoch pins a model here.
"""

from __future__ import annotations

import json

_SYSTEM = (
    "You are a radio verbalizer for an ATC simulator. Given a JSON 'intent' describing "
    "exactly what a pilot means to say, output ONE realistic radio transmission string and "
    "nothing else. Say precisely the elements in the intent — never add, drop, or change a "
    "number, callsign, or instruction. Match the persona's tone (airline_crisp = terse; "
    "student_pilot = hesitant, verbose; foreign_carrier = formal ICAO; ga_relaxed = casual; "
    "unfamiliar = asks for help). Use standard phraseology and spoken numbers."
)


class LLMVerbalizerBackend:  # pragma: no cover - requires network + key
    def __init__(self, model_id: str, max_tokens: int = 120):
        from anthropic import Anthropic

        self._client = Anthropic()
        self.model_id = model_id
        self.max_tokens = max_tokens

    def render(self, intent: dict) -> str:
        resp = self._client.messages.create(
            model=self.model_id,
            max_tokens=self.max_tokens,
            temperature=0,
            system=_SYSTEM,
            messages=[{"role": "user", "content": json.dumps(intent, sort_keys=True)}],
        )
        return "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
