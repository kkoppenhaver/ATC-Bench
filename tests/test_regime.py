"""Time regimes: token-metering mechanics and the tempo tradeoff (DESIGN §4.2)."""

from __future__ import annotations

import pytest

from atcbench.harness.adapters import ReasoningController, ReplayAdapter, ScriptedCDController
from atcbench.harness.ground_session import GroundSession
from atcbench.harness.regime import TokenMetered, TurnBased, make_regime
from atcbench.harness.session import CDSession
from atcbench.scenarios import cd as cd_scenarios
from atcbench.scenarios import gnd as gnd_scenarios
from atcbench.scoring.cd import score_cd
from atcbench.scoring.gnd import score_gnd


def test_regime_math():
    assert TurnBased().thinking_seconds(9999) == 0
    m = TokenMetered()  # R = 25
    assert m.thinking_seconds(0) == 0
    assert m.thinking_seconds(25) == 1
    assert m.thinking_seconds(26) == 2
    assert m.thinking_seconds(800) == 32
    assert make_regime("metered").name == "metered"
    assert make_regime("turn").name == "turn"


def _score_cd(adapter, seed, regime):
    scn = cd_scenarios.generate(seed, band="standard")
    res = CDSession(scn, regime=regime).run(adapter)
    return score_cd(res.log, scn.to_dict()), res


@pytest.mark.parametrize("seed", [1, 7, 42])
def test_oracle_certifies_both_regimes_cd(seed):
    # A terse controller spends negligible thinking time, so metering doesn't hurt it.
    turn, _ = _score_cd(ScriptedCDController(), seed, TurnBased())
    metered, _ = _score_cd(ScriptedCDController(), seed, TokenMetered())
    assert turn["gate"] == 1 and metered["gate"] == 1


def test_verbose_model_pays_a_tempo_penalty():
    # A heavy reasoner (800 thinking tokens/turn -> 32 s) blows the 30 s correction
    # window under metering, so its metered score is strictly worse than turn-based.
    turn, _ = _score_cd(ReasoningController(ScriptedCDController(), 800), 42, TurnBased())
    metered, _ = _score_cd(ReasoningController(ScriptedCDController(), 800), 42, TokenMetered())
    assert turn["gate"] == 1
    assert metered["S"] < turn["S"]


def test_metered_replay_is_byte_identical():
    scn = cd_scenarios.generate(42, band="standard")
    live = CDSession(scn, regime=TokenMetered()).run(ReasoningController(ScriptedCDController(), 400))
    scn2 = cd_scenarios.generate(42, band="standard")
    replay = CDSession(scn2, regime=TokenMetered()).run(
        ReplayAdapter([t["output"] for t in live.model_io]))
    assert replay.log.to_jsonl() == live.log.to_jsonl()


@pytest.mark.parametrize("seed", [1, 7, 42])
def test_oracle_certifies_both_regimes_gnd(seed):
    from atcbench.harness.adapters import ScriptedGNDController

    scn = gnd_scenarios.generate(seed, band="standard")
    turn = score_gnd(GroundSession(scn, regime=TurnBased()).run(ScriptedGNDController()).log,
                     scn.to_dict())
    scn2 = gnd_scenarios.generate(seed, band="standard")
    metered = score_gnd(GroundSession(scn2, regime=TokenMetered()).run(ScriptedGNDController()).log,
                        scn2.to_dict())
    assert turn["gate"] == 1 and metered["gate"] == 1
