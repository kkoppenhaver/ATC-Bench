"""Live-model path (audit C3, DESIGN §11.1, §17.4).

The AnthropicAdapter is exercised against a fake client: real tool results attached
to the next request in one alternating user message, retry/backoff on transient
errors, and a hard USD budget that stops API calls and flags the run record. The
session side is exercised for the bay_read round-trip (strips must be readable, not
write-only) and verbatim I/O logging.
"""

from __future__ import annotations

import json

import pytest

from atcbench.harness.adapters import AnthropicAdapter, ModelAdapter, ScriptedCDController
from atcbench.harness.session import CDSession
from atcbench.harness.system_prompt import PROMPT_TEMPLATE_VERSIONS, build_system_prompt
from atcbench.scenarios import cd as cd_scenarios


class _Block:
    def __init__(self, **kw):
        self._d = dict(kw)
        for k, v in kw.items():
            setattr(self, k, v)

    def model_dump(self):
        return dict(self._d)


class _Usage:
    def __init__(self, input_tokens=100, output_tokens=50,
                 cache_read_input_tokens=0, cache_creation_input_tokens=0):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_read_input_tokens = cache_read_input_tokens
        self.cache_creation_input_tokens = cache_creation_input_tokens


class _Resp:
    def __init__(self, content, usage=None):
        self.content = content
        self.usage = usage or _Usage()


class _FakeClient:
    """Plays a scripted list of responses (or exceptions) and records requests."""

    def __init__(self, script):
        outer = self

        class _Messages:
            def create(self, **kw):
                import copy

                outer.calls.append(copy.deepcopy(kw))  # snapshot, like real serialization
                item = outer.script.pop(0)
                if isinstance(item, Exception):
                    raise item
                return item

        self.script = list(script)
        self.calls: list[dict] = []
        self.messages = _Messages()


def _adapter(script, **kw):
    return AnthropicAdapter("test-model", "system", [], client=_FakeClient(script), **kw)


def test_tool_results_attach_to_next_request_with_alternating_roles():
    tool_use = _Block(type="tool_use", id="t1", name="bay_read", input={})
    a = _adapter([_Resp([tool_use]), _Resp([_Block(type="text", text="ok")])])
    out = a.step({"tick": 0})
    assert out["tool_calls"] == [{"name": "bay_read", "input": {}}]
    a.receive_tool_results(['{"bays": {"queue": ["AAL1"]}}'])
    a.step({"tick": 5})

    second_request = a._client.calls[1]["messages"]
    roles = [m["role"] for m in second_request]
    assert roles == ["user", "assistant", "user"]  # strict alternation
    last_user = second_request[-1]["content"]
    assert last_user[0]["type"] == "tool_result"
    assert last_user[0]["tool_use_id"] == "t1"
    assert "AAL1" in last_user[0]["content"]  # the real bay contents, not "ok"
    assert last_user[1]["type"] == "text" and '"tick": 5' in last_user[1]["text"]


def test_budget_exhaustion_stops_api_calls_and_flags_turns():
    tool_use = _Block(type="tool_use", id="t1", name="wait", input={})
    a = _adapter([_Resp([tool_use], _Usage(1_000_000, 1_000_000))],
                 max_usd=0.01, usd_per_mtok_in=3.0, usd_per_mtok_out=15.0)
    a.step({"tick": 0})
    assert a.budget_exhausted
    assert a.spent_usd() == pytest.approx(18.0)
    out = a.step({"tick": 5})
    assert out["budget_exhausted"] and out["tool_calls"][0]["name"] == "wait"
    assert len(a._client.calls) == 1  # no further API spend


def test_budget_accounting_is_cache_aware():
    # Cache reads bill at ~0.1x input price, writes at ~1.25x — the cap must track
    # real spend, not raw token counts, and requests must request auto-caching.
    tool_use = _Block(type="tool_use", id="t1", name="wait", input={})
    a = _adapter([_Resp([tool_use], _Usage(100_000, 10_000,
                                           cache_read_input_tokens=1_000_000,
                                           cache_creation_input_tokens=100_000))],
                 usd_per_mtok_in=1.0, usd_per_mtok_out=5.0)
    a.step({"tick": 0})
    # (0.1M + 1.25*0.1M + 0.1*1M) * $1/M + 0.01M * $5/M = 0.325 + 0.05
    assert a.spent_usd() == pytest.approx(0.375)
    assert a._client.calls[0]["cache_control"] == {"type": "ephemeral"}


def test_transient_errors_are_retried(monkeypatch):
    import time as time_mod

    monkeypatch.setattr(time_mod, "sleep", lambda s: None)

    class _RateLimited(Exception):
        status_code = 429

    ok = _Resp([_Block(type="text", text="fine")])
    a = _adapter([_RateLimited(), _RateLimited(), ok], max_retries=3)
    out = a.step({"tick": 0})
    assert out["text"] == "fine"

    class _BadRequest(Exception):
        status_code = 400

    b = _adapter([_BadRequest()], max_retries=3)
    with pytest.raises(_BadRequest):
        b.step({"tick": 0})


class _StripReader(ModelAdapter):
    """Writes a strip note, reads the bay back, then waits forever."""

    def __init__(self):
        self.received: list[list[str]] = []
        self.done = False

    def step(self, obs):
        if not self.done and obs["aircraft"]:
            self.done = True
            acid = obs["aircraft"][0]["acid"]
            return {"tool_calls": [
                {"name": "strip_update", "input": {"acid": acid, "patch": {"note": "hot"}}},
                {"name": "bay_read", "input": {}},
                {"name": "wait", "input": {}},
            ], "text": "", "output_tokens": 1}
        return self.wait()

    def receive_tool_results(self, results):
        self.received.append(results)


def test_bay_read_returns_real_contents_not_write_only():
    scn = cd_scenarios.generate(1, band="standard", session_seconds=3600)
    adapter = _StripReader()
    CDSession(scn).run(adapter)
    first = next(r for r in adapter.received if len(r) >= 2)
    assert "updated" in first[0]
    bay = json.loads(first[1])
    assert any(s.get("note") == "hot" for s in bay["strips"].values())


def test_model_io_records_verbatim_observations_and_results():
    scn = cd_scenarios.generate(1, band="standard", session_seconds=3600)
    res = CDSession(scn).run(ScriptedCDController())
    assert res.model_io
    for turn in res.model_io:
        assert "observation" in turn and "output" in turn and "tool_results" in turn


def test_system_prompts_exist_per_position_with_versioned_hashes():
    seen = set()
    for pos in ("CD", "GND", "TWR"):
        text, ph = build_system_prompt(pos, 3600, "turn")
        assert ph.startswith(PROMPT_TEMPLATE_VERSIONS[pos] + ":")
        assert text and ph not in seen
        seen.add(ph)
    gnd_text, _ = build_system_prompt("GND", 3600, "turn")
    assert "hold all crossings" in gnd_text  # coordination protocol is taught
    twr_text, _ = build_system_prompt("TWR", 3600, "turn")
    assert "H->L: 120s" in twr_text  # wake matrix is in the prompt


def _text_resp(txt="ok", **usage_kw):
    return _Resp([_Block(type="text", text=txt)], usage=_Usage(**usage_kw))


def test_context_window_trims_oldest_turns_proactively():
    # Trigger tiny so the second turn's prompt size (100 input tokens) trips a trim
    # before the third request; target 1 forces the trim to cut to the 2-message floor.
    tool_use = _Block(type="tool_use", id="t1", name="bay_read", input={})
    a = _adapter([_Resp([tool_use]), _text_resp("two"), _text_resp("three")],
                 context_trim_trigger=10, context_trim_target=1)
    a.step({"tick": 0})
    a.receive_tool_results(["bay contents"])
    a.step({"tick": 5})
    out = a.step({"tick": 10})
    assert out["context_trimmed"] == 2 and a.context_trims == 1
    msgs = a._client.calls[-1]["messages"]
    # Alternation survives, the head is a sanitized user message with a trim marker
    # and no tool_result orphaned against a dropped assistant turn.
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
    head = msgs[0]["content"]
    assert head[0]["type"] == "text" and "trimmed" in head[0]["text"]
    assert all(b.get("type") != "tool_result" for b in head)


def test_context_overflow_400_trims_reactively_and_retries():
    class _TooLong(Exception):
        status_code = 400

        def __str__(self):
            return "Error code: 400 - prompt is too long: 200142 tokens > 200000 maximum"

    a = _adapter([_text_resp("one"), _text_resp("two"), _TooLong(), _text_resp("three")])
    a.step({"tick": 0})
    a.step({"tick": 5})
    before = len(a._client.calls[-1]["messages"])
    out = a.step({"tick": 10})
    assert out["text"] == "three" and a.context_trims == 1
    assert len(a._client.calls[-1]["messages"]) < before + 1  # history was cut, not grown
    assert [m["role"] for m in a._client.calls[-1]["messages"]][0] == "user"


def test_other_400s_still_raise():
    class _BadReq(Exception):
        status_code = 400

        def __str__(self):
            return "Error code: 400 - invalid tool schema"

    a = _adapter([_text_resp("one"), _BadReq()])
    a.step({"tick": 0})
    with pytest.raises(_BadReq):
        a.step({"tick": 5})
