"""Elicitation fairness (audit M5, DESIGN §7.2).

Unusable transmissions and formatting failures must be observable — a pilot
"say again" on frequency, a logged parse tier, a logged unparsed output — never a
silent drop that later reads as model inaction.
"""

from __future__ import annotations

from atcbench.harness.adapters import ModelAdapter
from atcbench.harness.session import CDSession
from atcbench.harness.tower_session import TowerSession
from atcbench.scenarios import cd as cd_scenarios
from atcbench.scenarios import twr as twr_scenarios
from atcbench.verbalizer.template import _callsign_words


class _OneShot(ModelAdapter):
    """Performs one scripted action on the first turn with traffic, then waits."""

    def __init__(self) -> None:
        self.done = False

    def step(self, observation: dict) -> dict:
        if not self.done and observation["aircraft"]:
            self.done = True
            return self.act(observation)
        return self.wait()


class _GarbageTransmitter(_OneShot):
    def act(self, obs: dict) -> dict:
        cs = _callsign_words(obs["aircraft"][0]["acid"])
        return self.transmit(f"{cs}, the weather sure is nice today.")


class _PlainTextResponder(_OneShot):
    def act(self, obs: dict) -> dict:
        return {"tool_calls": [], "text": "I think I should probably issue a clearance.",
                "output_tokens": 12}


def test_unusable_transmission_draws_say_again():
    scn = cd_scenarios.generate(1, band="standard", session_seconds=3600)
    res = CDSession(scn).run(_GarbageTransmitter())
    says = res.log.of_type("say_again")
    assert says, "addressed-but-unusable transmission must trigger a pilot say_again"
    acid = says[0].payload["acid"]
    assert any(t["from"] == acid and "Say again" in t["text"] for t in res.transcript)


def test_plain_text_without_tool_call_is_logged():
    scn = cd_scenarios.generate(1, band="standard", session_seconds=3600)
    res = CDSession(scn).run(_PlainTextResponder())
    unparsed = res.log.of_type("unparsed_model_output")
    assert unparsed
    assert "issue a clearance" in unparsed[0].payload["text"]


def test_every_controller_transmission_gets_a_parse_tier():
    from atcbench.harness.adapters import ScriptedCDController

    scn = cd_scenarios.generate(1, band="standard", session_seconds=3600)
    res = CDSession(scn).run(ScriptedCDController())
    ctrl_tx = [e for e in res.log.of_type("transmission")
               if e.payload["speaker"].endswith("_CD")]
    parses = res.log.of_type("controller_parse")
    assert len(parses) == len(ctrl_tx)
    assert all(1 <= e.payload["tier"] <= 4 for e in parses)
    # The oracle speaks standard phraseology: everything it says parses tier 1.
    assert all(e.payload["tier"] == 1 for e in parses)


def test_twr_wrong_phase_instruction_draws_say_again():
    class _TakeoffToArrival(_OneShot):
        def act(self, obs: dict) -> dict:
            arr = next(a for a in obs["aircraft"] if a["role"] == "arrival")
            cs = _callsign_words(arr["acid"])
            return self.transmit(f"{cs}, runway three one center, cleared for takeoff.")

    scn = twr_scenarios.generate(1)
    res = TowerSession(scn).run(_TakeoffToArrival())
    assert res.log.of_type("say_again")
