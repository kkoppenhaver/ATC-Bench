"""Model adapters (DESIGN §11.1, §17.3).

An adapter turns a per-turn observation into a list of tool calls. The harness is a
stateless translator around it. Three adapters ship for the CD slice:

- ``ScriptedCDController`` — a deterministic oracle controller. Plays the model role
  with no API, so the whole loop runs offline and can gate scenario feasibility.
- ``BadCDController`` — issues clearances but never closes the readback loop; used to
  show that busts are detected (Phase 2's "bad controller" idea, previewed for CD).
- ``ReplayAdapter`` — replays recorded per-turn outputs; the basis of the determinism
  contract (§17.2): same recorded outputs -> byte-identical event logs.
- ``AnthropicAdapter`` — real model under test (requires the ``anthropic`` extra).

Adapter output is a dict: ``{"tool_calls": [{"name", "input"}], "text", "output_tokens"}``.
"""

from __future__ import annotations

import math
from typing import Any

from ..charts import kmdw_cd
from ..verbalizer.template import _callsign_words, spoken_altitude, spoken_digits


def _est_tokens(text: str) -> int:
    return max(1, math.ceil(len(text) / 4))


def _correct_clearance_for(ac: dict) -> dict:
    filed = ac["filed"]
    sid = filed["sid"]
    route = sid if kmdw_cd.PACK.sid_valid(sid) else "MDW7"
    return {
        "destination": filed["destination"],
        "route": route,
        "altitude": kmdw_cd.LOA_INITIAL_ALTITUDE,
        "frequency": kmdw_cd.DEPARTURE_FREQUENCY,
        "squawk": ac["assigned_squawk"],
    }


class ModelAdapter:
    def step(self, observation: dict) -> dict:  # pragma: no cover - interface
        raise NotImplementedError

    def transmit(self, text: str) -> dict:
        return {
            "tool_calls": [{"name": "transmit", "input": {"text": text}}],
            "text": "",
            "output_tokens": _est_tokens(text),
        }

    @staticmethod
    def wait() -> dict:
        return {"tool_calls": [{"name": "wait", "input": {}}], "text": "", "output_tokens": 1}


class ScriptedCDController(ModelAdapter):
    """Deterministic oracle: correct clearances, catches every catchable readback error."""

    def __init__(self) -> None:
        self._issued: set[str] = set()

    def step(self, observation: dict) -> dict:
        for ac in observation["aircraft"]:
            acid = ac["acid"]
            if ac["status"] == "awaiting_clearance" and acid not in self._issued:
                self._issued.add(acid)
                return self.transmit(self._clearance_text(ac))
            if ac["status"] == "readback_pending" and ac.get("last_readback"):
                corr = self._correction_text(ac)
                if corr:
                    return self.transmit(corr)
        return self.wait()

    def _clearance_text(self, ac: dict) -> str:
        c = _correct_clearance_for(ac)
        cs = _callsign_words(ac["acid"])
        dest_name = ac["filed"]["destination_name"]
        sid = kmdw_cd.SIDS.get(c["route"], {"name": c["route"]})["name"]
        return (
            f"{cs}, cleared to {dest_name}, {sid} departure, "
            f"maintain {spoken_altitude(c['altitude'])}, "
            f"departure {spoken_digits(c['frequency'])}, "
            f"squawk {spoken_digits(c['squawk'])}."
        )

    def _correction_text(self, ac: dict) -> str | None:
        c = _correct_clearance_for(ac)
        rb = ac["last_readback"]
        cs = _callsign_words(ac["acid"])
        if rb.get("dropped"):
            return (
                f"{cs}, I need a full readback, maintain {spoken_altitude(c['altitude'])}, "
                f"squawk {spoken_digits(c['squawk'])}."
            )
        if rb.get("callsign_used") and rb["callsign_used"] != ac["acid"]:
            return f"{cs}, verify, squawk {spoken_digits(c['squawk'])}."
        if rb.get("altitude") != c["altitude"]:
            return f"{cs}, negative, maintain {spoken_altitude(c['altitude'])}."
        if rb.get("frequency") != c["frequency"]:
            return f"{cs}, negative, departure {spoken_digits(c['frequency'])}."
        if rb.get("squawk") != c["squawk"]:
            return f"{cs}, negative, squawk {spoken_digits(c['squawk'])}."
        return None


class BadCDController(ModelAdapter):
    """Issues clearances but never closes the readback loop (busts on uncaught errors)."""

    def __init__(self) -> None:
        self._issued: set[str] = set()

    def step(self, observation: dict) -> dict:
        for ac in observation["aircraft"]:
            acid = ac["acid"]
            if ac["status"] == "awaiting_clearance" and acid not in self._issued:
                self._issued.add(acid)
                c = _correct_clearance_for(ac)
                cs = _callsign_words(acid)
                sid = kmdw_cd.SIDS.get(c["route"], {"name": c["route"]})["name"]
                text = (
                    f"{cs}, cleared to {ac['filed']['destination_name']}, {sid} departure, "
                    f"maintain {spoken_altitude(c['altitude'])}, "
                    f"departure {spoken_digits(c['frequency'])}, "
                    f"squawk {spoken_digits(c['squawk'])}."
                )
                return self.transmit(text)
        return self.wait()


_RWY_WORD = {"l": "left", "r": "right", "c": "center"}


def _runway_spoken(rwy: str) -> str:
    digits, suffix = rwy[:-1], rwy[-1].lower()
    return f"{spoken_digits(digits)} {_RWY_WORD.get(suffix, suffix)}"


class ScriptedGNDController(ModelAdapter):
    """Oracle ground controller: routes departures via A holding short of 31R, clears the
    31R crossing only when the runway is idle with a safe gap, routes arrivals via B."""

    CROSSING_SAFE_GAP_SEC = 40

    def step(self, observation: dict) -> dict:
        for ac in observation["aircraft"]:
            cs = _callsign_words(ac["acid"])
            if not ac["route_assigned"]:
                if ac["role"] == "departure":
                    return self.transmit(
                        f"{cs}, runway {_runway_spoken('31C')}, taxi via alpha, "
                        f"hold short runway {_runway_spoken('31R')}.")
                return self.transmit(f"{cs}, taxi to the gate via bravo.")
        for ac in observation["aircraft"]:
            if ac["role"] == "departure" and ac["holding_short_of"] == "31R":
                rw = observation["runways"].get("31R", {})
                gap = rw.get("next_hot_in")
                if not rw.get("hot") and (gap is None or gap > self.CROSSING_SAFE_GAP_SEC):
                    cs = _callsign_words(ac["acid"])
                    return self.transmit(f"{cs}, cross runway {_runway_spoken('31R')}.")
        return self.wait()


class BadGNDController(ModelAdapter):
    """Routes arrivals against the departure flow (via A) and clears crossings blindly —
    reliably produces a head-on deadlock and/or a runway incursion."""

    def step(self, observation: dict) -> dict:
        for ac in observation["aircraft"]:
            cs = _callsign_words(ac["acid"])
            if not ac["route_assigned"]:
                if ac["role"] == "departure":
                    return self.transmit(f"{cs}, runway {_runway_spoken('31C')}, taxi via alpha.")
                return self.transmit(f"{cs}, taxi to the gate via alpha.")
        for ac in observation["aircraft"]:
            if ac["holding_short_of"] == "31R":
                cs = _callsign_words(ac["acid"])
                return self.transmit(f"{cs}, cross runway {_runway_spoken('31R')}.")
        return self.wait()


class ReplayAdapter(ModelAdapter):
    """Replays a recorded list of per-turn outputs (DESIGN §17.2)."""

    def __init__(self, recorded_turns: list[dict]) -> None:
        self._turns = list(recorded_turns)
        self._i = 0

    def step(self, observation: dict) -> dict:
        if self._i >= len(self._turns):
            return self.wait()
        out = self._turns[self._i]
        self._i += 1
        return out


class AnthropicAdapter(ModelAdapter):  # pragma: no cover - requires network + key
    """Real model under test via the Anthropic Messages API.

    Maintains the growing conversation itself (history management is the model's
    problem, §11.2). Requires the ``anthropic`` extra and an API key.
    """

    def __init__(self, model_id: str, system_prompt: str, tools: list[dict], max_tokens: int = 1024):
        import anthropic  # noqa: F401

        from anthropic import Anthropic

        self._client = Anthropic()
        self.model_id = model_id
        self.system_prompt = system_prompt
        self.tools = tools
        self.max_tokens = max_tokens
        self._messages: list[dict[str, Any]] = []

    def step(self, observation: dict) -> dict:
        import json

        self._messages.append({"role": "user", "content": json.dumps(observation)})
        resp = self._client.messages.create(
            model=self.model_id,
            max_tokens=self.max_tokens,
            system=self.system_prompt,
            tools=self.tools,
            messages=self._messages,
        )
        tool_calls = []
        text_parts = []
        assistant_content = []
        for block in resp.content:
            assistant_content.append(block.model_dump())
            if block.type == "tool_use":
                tool_calls.append({"name": block.name, "input": block.input})
            elif block.type == "text":
                text_parts.append(block.text)
        self._messages.append({"role": "assistant", "content": assistant_content})
        # Provide tool results so the conversation stays valid next turn.
        if tool_calls:
            self._messages.append(
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": b["id"], "content": "ok"}
                        for b in assistant_content
                        if b.get("type") == "tool_use"
                    ],
                }
            )
        return {
            "tool_calls": tool_calls or [{"name": "wait", "input": {}}],
            "text": "".join(text_parts),
            "output_tokens": resp.usage.output_tokens,
        }
