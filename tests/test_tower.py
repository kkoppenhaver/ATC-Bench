"""Tower position: certification, busts, provenance, determinism (DESIGN §6.3)."""

from __future__ import annotations

import pytest

from atcbench.baselines.feasibility import twr_feasible
from atcbench.harness.adapters import BadTWRController, ReplayAdapter, ScriptedTWRController
from atcbench.harness.regime import TokenMetered, TurnBased
from atcbench.harness.tower_session import TowerSession
from atcbench.scenarios import twr as twr_scenarios
from atcbench.scoring.twr import score_twr


def _score(adapter_cls, seed, band="standard", regime=None):
    scn = twr_scenarios.generate(seed, band=band)
    res = TowerSession(scn, regime=regime or TurnBased()).run(adapter_cls())
    return score_twr(res.log, scn.to_dict())


@pytest.mark.parametrize("seed", [1, 7, 42, 100, 2024])
@pytest.mark.parametrize("band", ["calm", "standard", "heavy"])
def test_oracle_certifies(seed, band):
    s = _score(ScriptedTWRController, seed, band)
    assert s["gate"] == 1
    assert s["counts"]["completed"] == s["counts"]["aircraft"]


def test_bad_controller_busts_across_seeds():
    busts = [_score(BadTWRController, s)["gate"] for s in range(1, 12)]
    assert all(g == 0 for g in busts)


def test_bad_controller_hits_los_or_wake():
    s = _score(BadTWRController, 42)
    kinds = {c["code"] for c in s["cardinal_violations"]}
    assert kinds & {"LOS", "WAKE"}


def test_forced_go_around_is_environment_provenance():
    # The seeded blown-tire go-around must not count against the model.
    s = _score(ScriptedTWRController, 42, "standard")
    assert s["counts"]["env_go_arounds"] >= 1
    assert s["gate"] == 1


def test_determinism_and_replay():
    scn = twr_scenarios.generate(42)
    a = TowerSession(scn).run(ScriptedTWRController())
    b = TowerSession(twr_scenarios.generate(42)).run(ScriptedTWRController())
    assert a.log.to_jsonl() == b.log.to_jsonl()
    replay = TowerSession(twr_scenarios.generate(42)).run(
        ReplayAdapter([t["output"] for t in a.model_io]))
    assert replay.log.to_jsonl() == a.log.to_jsonl()


def test_metered_oracle_still_certifies():
    assert _score(ScriptedTWRController, 42, "standard", TokenMetered())["gate"] == 1


@pytest.mark.parametrize("seed", [1, 7, 42, 100, 2024])
def test_scenarios_feasible(seed):
    assert twr_feasible(twr_scenarios.generate(seed, band="standard"))
