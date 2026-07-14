"""Scoring / certification behavior (DESIGN §13)."""

from __future__ import annotations

import pytest

from atcbench.harness.adapters import (
    BadCDController,
    BlindCDCorrector,
    DoNothingController,
    ScriptedCDController,
)
from atcbench.harness.session import CDSession
from atcbench.scenarios import cd as cd_scenarios
from atcbench.scoring.cd import score_cd


def _score(adapter_cls, seed, band="standard"):
    scn = cd_scenarios.generate(seed, band=band, session_seconds=3600)
    res = CDSession(scn).run(adapter_cls())
    return score_cd(res.log, scn.to_dict())


@pytest.mark.parametrize("seed", [1, 7, 42, 100])
@pytest.mark.parametrize("band", ["calm", "standard", "heavy"])
def test_oracle_certifies(seed, band):
    s = _score(ScriptedCDController, seed, band)
    assert s["gate"] == 1
    assert s["S"] == 1.0
    assert s["counts"]["caught_errors"] == s["counts"]["catchable_errors"]


def test_no_special_purpose_squawks_assigned():
    # 7500/7600/7700 are hijack/radio-failure/emergency codes (audit m5).
    from atcbench.scenarios.cd import SPECIAL_SQUAWKS

    for seed in range(1, 25):
        scn = cd_scenarios.generate(seed, band="heavy", session_seconds=3600)
        squawks = {c["squawk"] for c in scn.expected_clearance.values()}
        assert not (squawks & SPECIAL_SQUAWKS)


def test_bad_controller_busts():
    s = _score(BadCDController, 42)
    assert s["gate"] == 0
    assert s["S"] == 0.0
    assert s["counts"]["cardinals"] >= 1


@pytest.mark.parametrize("seed", [1, 7, 42])
def test_do_nothing_never_certifies(seed):
    # No-skill probe (X.5): pure inaction must bust on NEGLECT, never score.
    s = _score(DoNothingController, seed)
    assert s["gate"] == 0
    assert s["S"] == 0.0
    assert any(c["code"] == "NEGLECT" for c in s["cardinal_violations"])


@pytest.mark.parametrize("seed", [1, 7, 42, 100])
def test_blind_corrector_scores_zero_hearback(seed):
    # Low-skill probe (X.5, audit C2): correcting every readback without listening
    # used to match the oracle's S=1.0. It must score H=0 (all false alarms) and sit
    # well below the oracle; the cert-level assertion arrives with P3.6.3.
    s = _score(BlindCDCorrector, seed)
    assert s["components"]["H"] == 0.0
    assert s["counts"]["spurious_corrections"] >= 1
    assert s["S"] <= 0.80


def test_bad_controller_busts_across_seeds():
    # A controller that never closes the readback loop should bust wherever the
    # seed schedules a safety-critical (altitude) readback error.
    busts = [_score(BadCDController, s)["gate"] for s in range(1, 12)]
    assert busts.count(0) >= 6
