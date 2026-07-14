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
import re
from typing import Any

from ..verbalizer.template import _callsign_words, spoken_altitude, spoken_digits


def _est_tokens(text: str) -> int:
    return max(1, math.ceil(len(text) / 4))


def _correct_clearance_for(ac: dict, pack: dict) -> dict:
    """Derive the correct clearance from the briefed chart pack (audit M6): the
    answers are chart lookups against per-scenario material, not constants."""
    filed = ac["filed"]
    sid = filed["sid"]
    route = sid if sid in pack["sids"] else pack["fallback_sid"]
    return {
        "destination": filed["destination"],
        "route": route,
        "altitude": pack["sids"][route]["initial_altitude"],
        "frequency": pack["departure_frequency"],
        "squawk": ac["assigned_squawk"],
    }


class ModelAdapter:
    def step(self, observation: dict) -> dict:  # pragma: no cover - interface
        raise NotImplementedError

    def receive_tool_results(self, results: list[str]) -> None:
        """Called by the session after applying a turn's tool calls, with one result
        string per applied call (bay contents for bay_read, acks otherwise). Scripted
        adapters don't need them; API adapters attach them to the next request."""

    def brief(self, chart_pack: dict) -> None:
        """Position briefing before the session: the per-scenario chart pack (§11.3).
        Scripted controllers store it to derive correct clearances; live adapters
        ignore it — their briefing is the system prompt."""
        self._pack = chart_pack

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
    """Deterministic oracle: correct clearances, catches every catchable readback error.

    Works hearback from the frequency feed alone (raw representation, §11.2): it reads
    the pilot's actual radio call, never a pre-parsed readback. A dropped readback is
    heard as silence — cleared aircraft that said nothing back get a prompt."""

    def __init__(self) -> None:
        self._issued: set[str] = set()
        self._prompted: set[str] = set()
        self._heard: dict[str, str] = {}   # acid -> latest transmission since clearance
        self._judged: dict[str, str] = {}  # acid -> last transmission already acted on

    def step(self, observation: dict) -> dict:
        position = observation.get("position")
        for msg in observation["frequency"]:
            if msg.get("from") != position:
                self._heard[msg["from"]] = msg["text"]
        for ac in observation["aircraft"]:
            acid = ac["acid"]
            if ac["status"] == "awaiting_clearance" and acid not in self._issued:
                self._issued.add(acid)
                self._heard.pop(acid, None)  # forget the check-in; listen for the readback
                return self.transmit(self._clearance_text(ac))
            if ac["status"] == "readback_pending" and acid in self._issued:
                text = self._heard.get(acid)
                if text is None:
                    # Cleared, and nothing came back on frequency: dropped readback.
                    if acid not in self._prompted:
                        self._prompted.add(acid)
                        c = _correct_clearance_for(ac, self._pack)
                        cs = _callsign_words(acid)
                        return self.transmit(
                            f"{cs}, I need a full readback, "
                            f"maintain {spoken_altitude(c['altitude'])}, "
                            f"squawk {spoken_digits(c['squawk'])}.")
                    continue
                if self._judged.get(acid) == text:
                    continue
                self._judged[acid] = text
                corr = self._correction_text(ac, text)
                if corr:
                    return self.transmit(corr)
        return self.wait()

    def _clearance_text(self, ac: dict) -> str:
        c = _correct_clearance_for(ac, self._pack)
        cs = _callsign_words(ac["acid"])
        dest_name = ac["filed"]["destination_name"]
        sid = self._pack["sids"][c["route"]]["name"]
        return (
            f"{cs}, cleared to {dest_name}, {sid} departure, "
            f"maintain {spoken_altitude(c['altitude'])}, "
            f"departure {spoken_digits(c['frequency'])}, "
            f"squawk {spoken_digits(c['squawk'])}."
        )

    def _correction_text(self, ac: dict, text: str) -> str | None:
        """Judge a heard readback against the correct clearance — text in, words out."""
        from ..pilots import parser as P

        c = _correct_clearance_for(ac, self._pack)
        cs = _callsign_words(ac["acid"])
        # Wrong callsign: the telephony tail of the readback names someone else.
        m = re.match(r"[A-Za-z]+(\d+)", ac["acid"])
        if m:
            tail_digits = re.findall(r"\d+", P.normalize(text.rsplit(",", 1)[-1]))
            if tail_digits and tail_digits[-1] != m.group(1):
                return f"{cs}, verify, squawk {spoken_digits(c['squawk'])}."
        if P.extract_altitude(text) != c["altitude"]:
            return f"{cs}, negative, maintain {spoken_altitude(c['altitude'])}."
        if P.extract_frequency(text) != c["frequency"]:
            return f"{cs}, negative, departure {spoken_digits(c['frequency'])}."
        if P.extract_squawk(text) != c["squawk"]:
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
                c = _correct_clearance_for(ac, self._pack)
                cs = _callsign_words(acid)
                sid = self._pack["sids"][c["route"]]["name"]
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
    31R crossing only when the runway is idle with no Tower hold in effect, routes
    arrivals via B. Keeps its runway picture from the frequency feed alone (§4.5): Tower
    coordination calls announce upcoming runway use — there is no schedule field."""

    def __init__(self) -> None:
        self._tower_holds = 0  # coordination "hold" calls heard minus releases

    def step(self, observation: dict) -> dict:
        for msg in observation["frequency"]:
            text = msg.get("text", "").lower()
            if "hold all crossings" in text:
                self._tower_holds += 1
            elif "crossings at your discretion" in text:
                self._tower_holds = max(0, self._tower_holds - 1)
        if observation.get("channel_busy"):
            return self.wait()  # half-duplex: don't key up over traffic
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
                if not rw.get("hot") and self._tower_holds == 0:
                    cs = _callsign_words(ac["acid"])
                    return self.transmit(f"{cs}, cross runway {_runway_spoken('31R')}.")
        return self.wait()


class BadGNDController(ModelAdapter):
    """Routes arrivals against the departure flow (via A) and clears crossings blindly —
    reliably produces a head-on deadlock and/or a runway incursion."""

    def step(self, observation: dict) -> dict:
        if observation.get("channel_busy"):
            return self.wait()  # bad decisions, but it can still hear the radio
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


def _approach_per_sec(actype: str) -> float:
    from ..sim.performance import perf

    return perf(actype).approach_kt / 3600.0


class ScriptedTWRController(ModelAdapter):
    """Oracle Tower controller. Serializes the single runway — at most one committed use
    at a time (cleared_land / landing / luaw / takeoff) — so two aircraft never occupy it
    together. Clears an arrival only when wake separation holds at its threshold, and sends
    it around rather than clear it unsafely. Fits a departure into a gap only when the next
    arrival is far and wake permits, then hands airborne departures to Approach.

    Maintains its own runway-use picture (raw representation, §4.5): a takeoff use is
    recorded when the clearance goes out; a landing use when the arrival is first seen
    on the runway — up to one sweep late, which errs conservative. No derived
    since-last-use fields are read from the observation."""

    LAND_CLEAR_NM = 4.0
    DEP_ARR_CLEAR_NM = 5.0

    def __init__(self) -> None:
        self._last_use_start: int | None = None
        self._last_wake: str = "L"
        self._landings_seen: set[str] = set()

    def step(self, observation: dict) -> dict:
        from ..charts import kmrl_twr
        from ..sim.performance import wake_min_sec

        tick = observation["tick"]
        acs = observation["aircraft"]
        # Update the picture first (below) even on busy sweeps; but never key up
        # over traffic — half-duplex.
        channel_busy = observation.get("channel_busy", False)
        # Update the picture: an arrival first observed on the runway marks a use.
        for a in acs:
            if (a["role"] == "arrival" and a["phase"] == "landing"
                    and a["acid"] not in self._landings_seen):
                self._landings_seen.add(a["acid"])
                self._last_use_start = tick
                self._last_wake = a["wake"]
        since = (tick - self._last_use_start) if self._last_use_start is not None else None
        last_wake = self._last_wake
        if channel_busy:
            return self.wait()
        departures = [a for a in acs if a["role"] == "departure"]

        def eta(a):
            return a["dist_nm"] / _approach_per_sec(a["actype"])

        # Hand off airborne departures regardless of runway state.
        for d in departures:
            if d["phase"] == "airborne":
                cs = _callsign_words(d["acid"])
                return self.transmit(
                    f"{cs}, contact departure {spoken_digits(kmrl_twr.DEPARTURE_FREQUENCY)}.")

        committed = any(a["phase"] in ("cleared_land", "landing", "luaw", "takeoff") for a in acs)
        if committed:
            return self.wait()  # one runway use at a time

        finals = sorted((a for a in acs if a["role"] == "arrival" and a["phase"] == "final"), key=eta)
        if finals:
            a = finals[0]
            e = eta(a)
            wake_ok = since is None or (since + e) >= wake_min_sec(last_wake, a["wake"])
            if a["dist_nm"] <= self.LAND_CLEAR_NM:
                cs = _callsign_words(a["acid"])
                if wake_ok:
                    return self.transmit(f"{cs}, runway {_runway_spoken(kmrl_twr.RUNWAY)}, cleared to land.")
                if a["dist_nm"] <= kmrl_twr.GO_AROUND_NM + 0.5:
                    return self.transmit(f"{cs}, go around, I say again, go around.")
                return self.wait()
            # Nearest arrival still far — use the gap for a departure if it fits ahead.
            if a["dist_nm"] > self.DEP_ARR_CLEAR_NM:
                launch = self._launch_departure(tick, departures, since, last_wake, e, a["wake"],
                                                wake_min_sec, kmrl_twr)
                if launch:
                    return launch
            return self.wait()

        # No arrivals inbound: launch a departure if wake permits.
        launch = self._launch_departure(tick, departures, since, last_wake, 1e9, "L",
                                        wake_min_sec, kmrl_twr)
        return launch or self.wait()

    def _launch_departure(self, tick, departures, since, last_wake, nearest_eta, nearest_wake,
                          wake_min_sec, kmrl_twr):
        for d in departures:
            if d["phase"] != "hold_short":
                continue
            behind_ok = since is None or since >= wake_min_sec(last_wake, d["wake"])
            ahead_ok = nearest_eta >= max(kmrl_twr.OCCUPY_DEP_SEC + 30,
                                          wake_min_sec(d["wake"], nearest_wake))
            if behind_ok and ahead_ok:
                # Record the use at clearance time — the roll starts on readback.
                self._last_use_start = tick
                self._last_wake = d["wake"]
                cs = _callsign_words(d["acid"])
                return self.transmit(
                    f"{cs}, runway {_runway_spoken(kmrl_twr.RUNWAY)}, cleared for takeoff.")
        return None


class BadTWRController(ModelAdapter):
    """Clears every arrival to land and every departure for takeoff on sight — piling
    aircraft onto one runway. Produces simultaneous-occupancy LoS and wake busts."""

    def step(self, observation: dict) -> dict:
        from ..charts import kmrl_twr

        if observation.get("channel_busy"):
            return self.wait()  # bad decisions, but it can still hear the radio
        for a in observation["aircraft"]:
            cs = _callsign_words(a["acid"])
            if a["role"] == "arrival" and a["phase"] == "final":
                return self.transmit(f"{cs}, runway {_runway_spoken(kmrl_twr.RUNWAY)}, cleared to land.")
            if a["role"] == "departure" and a["phase"] == "hold_short":
                return self.transmit(f"{cs}, runway {_runway_spoken(kmrl_twr.RUNWAY)}, cleared for takeoff.")
            if a["role"] == "departure" and a["phase"] == "airborne":
                return self.transmit(f"{cs}, contact departure.")
        return self.wait()


class DoNothingController(ModelAdapter):
    """No-skill probe: waits every turn, at every position. Scoring integrity demands
    this controller never certifies anywhere — pure inaction must read as NEGLECT, not
    competence (X.5; 2026-07 audit finding C1)."""

    def step(self, observation: dict) -> dict:
        return self.wait()


class NoRouteGNDController(ModelAdapter):
    """Low-skill probe: says "taxi" with no route to everyone. Before the 2026-07
    audit the harness assigned the full canonical route anyway (verified exploit —
    routing without routing); pilots must instead ask for the route and the aircraft
    must end up NEGLECTed (X.5)."""

    def __init__(self) -> None:
        self._told: set[str] = set()

    def step(self, observation: dict) -> dict:
        if observation.get("channel_busy"):
            return self.wait()
        for ac in observation["aircraft"]:
            if ac["acid"] not in self._told:
                self._told.add(ac["acid"])
                cs = _callsign_words(ac["acid"])
                return self.transmit(f"{cs}, runway {_runway_spoken('31C')}, taxi.")
        return self.wait()


class TaxiOnlyGNDController(ScriptedGNDController):
    """Low-skill probe: routes everyone like the oracle but never clears the 31R
    crossing — stranding every departure at the hold bar scored S=0.80 with gate=1
    before the 2026-07 audit; it must read as NEGLECT (X.5)."""

    def step(self, observation: dict) -> dict:
        for ac in observation["aircraft"]:
            if not ac["route_assigned"]:
                return super().step(observation)
        return self.wait()


class BlindCDCorrector(ModelAdapter):
    """Low-skill probe: transmits the full correct clearance as a "negative ..."
    correction to every pending readback without ever reading one. Before the 2026-07
    audit (finding C2) this matched the oracle's S=1.0; scoring integrity demands it
    score H=0 — it catches every error but false-alarms every correct readback (X.5)."""

    def __init__(self) -> None:
        self._issued: set[str] = set()
        self._corrected: set[str] = set()

    def step(self, observation: dict) -> dict:
        for ac in observation["aircraft"]:
            acid = ac["acid"]
            if ac["status"] == "awaiting_clearance" and acid not in self._issued:
                self._issued.add(acid)
                return self.transmit(self._full_text(ac, prefix=""))
            if ac["status"] == "readback_pending" and acid not in self._corrected:
                self._corrected.add(acid)
                return self.transmit(self._full_text(ac, prefix="negative, "))
        return self.wait()

    def _full_text(self, ac: dict, prefix: str) -> str:
        c = _correct_clearance_for(ac, self._pack)
        cs = _callsign_words(ac["acid"])
        sid = self._pack["sids"][c["route"]]["name"]
        return (
            f"{cs}, {prefix}cleared to {ac['filed']['destination_name']}, {sid} departure, "
            f"maintain {spoken_altitude(c['altitude'])}, "
            f"departure {spoken_digits(c['frequency'])}, "
            f"squawk {spoken_digits(c['squawk'])}."
        )


class ReasoningController(ModelAdapter):
    """Wraps any controller and inflates its reported output tokens by a fixed amount,
    standing in for a model that reasons at length before each action. Under the
    token-metered regime (§4.2) this makes the wrapped controller fall behind the
    traffic; under turn-based it costs nothing — which is exactly the tempo tradeoff."""

    def __init__(self, base: ModelAdapter, thinking_tokens: int = 800):
        self.base = base
        self.thinking_tokens = thinking_tokens

    def brief(self, chart_pack: dict) -> None:
        self.base.brief(chart_pack)

    def receive_tool_results(self, results: list[str]) -> None:
        self.base.receive_tool_results(results)

    def step(self, observation: dict) -> dict:
        resp = dict(self.base.step(observation))
        resp["output_tokens"] = int(resp.get("output_tokens", 0)) + self.thinking_tokens
        return resp


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


class AnthropicAdapter(ModelAdapter):
    """Real model under test via the Anthropic Messages API (audit C3).

    Maintains the growing conversation itself (history management is the model's
    problem, §11.2). The session pushes real tool results (bay contents on
    ``bay_read``, acks otherwise) via ``receive_tool_results``; they are attached to
    the *next* request in the same user message as the new observation, preserving
    strict role alternation. Includes retry/backoff for transient API errors and an
    optional hard USD budget (§17.4): once exhausted, the adapter stops calling the
    API and waits out the session, flagging every turn ``budget_exhausted`` so the
    run record shows truncation rather than incompetence.
    """

    RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504, 529}

    def __init__(self, model_id: str, system_prompt: str, tools: list[dict],
                 max_tokens: int = 1024, max_usd: float | None = None,
                 usd_per_mtok_in: float | None = None, usd_per_mtok_out: float | None = None,
                 max_retries: int = 5, client: Any = None):
        if client is None:  # pragma: no cover - requires the anthropic extra + key
            from anthropic import Anthropic

            client = Anthropic()
        self._client = client
        self.model_id = model_id
        self.system_prompt = system_prompt
        self.tools = tools
        self.max_tokens = max_tokens
        self.max_usd = max_usd
        self.usd_per_mtok_in = usd_per_mtok_in
        self.usd_per_mtok_out = usd_per_mtok_out
        self.max_retries = max_retries
        self._messages: list[dict[str, Any]] = []
        self._pending_tool_ids: list[str] = []
        self._pending_results: list[str] | None = None
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_cache_read_tokens = 0
        self.total_cache_write_tokens = 0
        self.budget_exhausted = False

    # --- budget ---------------------------------------------------------------

    def spent_usd(self) -> float | None:
        """Cache-aware spend: reads bill at ~0.1x input price, writes at ~1.25x."""
        if self.usd_per_mtok_in is None or self.usd_per_mtok_out is None:
            return None
        weighted_in = (self.total_input_tokens
                       + 1.25 * self.total_cache_write_tokens
                       + 0.1 * self.total_cache_read_tokens)
        return (weighted_in * self.usd_per_mtok_in
                + self.total_output_tokens * self.usd_per_mtok_out) / 1_000_000

    # --- session hook ----------------------------------------------------------

    def receive_tool_results(self, results: list[str]) -> None:
        self._pending_results = list(results)

    # --- turn ------------------------------------------------------------------

    def step(self, observation: dict) -> dict:
        import json

        if self.budget_exhausted:
            return {"tool_calls": [{"name": "wait", "input": {}}], "text": "",
                    "output_tokens": 0, "budget_exhausted": True}

        content: list[dict] = []
        if self._pending_tool_ids:
            results = self._pending_results or []
            for i, tid in enumerate(self._pending_tool_ids):
                content.append({"type": "tool_result", "tool_use_id": tid,
                                "content": results[i] if i < len(results) else "ok"})
        content.append({"type": "text", "text": json.dumps(observation, sort_keys=True)})
        self._messages.append({"role": "user", "content": content})
        self._pending_tool_ids = []
        self._pending_results = None

        resp = self._create_with_retry()
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
        self._pending_tool_ids = [b["id"] for b in assistant_content
                                  if b.get("type") == "tool_use"]

        self.total_input_tokens += resp.usage.input_tokens
        self.total_output_tokens += resp.usage.output_tokens
        self.total_cache_read_tokens += getattr(resp.usage, "cache_read_input_tokens", 0) or 0
        self.total_cache_write_tokens += getattr(resp.usage, "cache_creation_input_tokens", 0) or 0
        spent = self.spent_usd()
        if self.max_usd is not None and spent is not None and spent >= self.max_usd:
            self.budget_exhausted = True

        return {
            "tool_calls": tool_calls or [{"name": "wait", "input": {}}],
            "text": "".join(text_parts),
            "output_tokens": resp.usage.output_tokens,
            "input_tokens": resp.usage.input_tokens,
        }

    def _create_with_retry(self):
        import time

        delay = 1.0
        for attempt in range(self.max_retries + 1):
            try:
                # The conversation is append-only, so the prefix is byte-stable:
                # auto-caching the last cacheable block makes each turn re-read the
                # history at ~0.1x instead of re-billing it in full.
                return self._client.messages.create(
                    model=self.model_id,
                    max_tokens=self.max_tokens,
                    cache_control={"type": "ephemeral"},
                    system=self.system_prompt,
                    tools=self.tools,
                    messages=self._messages,
                )
            except Exception as e:  # noqa: BLE001 - classified below
                status = getattr(e, "status_code", None)
                retryable = (status in self.RETRYABLE_STATUS
                             or "connection" in type(e).__name__.lower()
                             or "timeout" in type(e).__name__.lower())
                if attempt >= self.max_retries or not retryable:
                    raise
                time.sleep(delay)
                delay = min(delay * 2, 30.0)
        raise RuntimeError("unreachable")  # pragma: no cover
