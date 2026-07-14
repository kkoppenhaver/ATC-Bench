"""Ground position: determinism, oracle certification, bad-controller busts, feasibility.

Mirrors the Phase 2 exit test (DESIGN §15): the oracle certifies at the standard band
and the scripted bad controller busts reliably.
"""

from __future__ import annotations

import pytest

from atcbench.baselines.feasibility import gnd_feasible
from atcbench.harness.adapters import (
    BadGNDController,
    DoNothingController,
    ReplayAdapter,
    ScriptedGNDController,
)
from atcbench.harness.ground_session import GroundSession
from atcbench.pilots import parser as P
from atcbench.scenarios import gnd as gnd_scenarios
from atcbench.scoring.gnd import score_gnd


def _score(adapter_cls, seed, band="standard"):
    scn = gnd_scenarios.generate(seed, band=band)
    res = GroundSession(scn).run(adapter_cls())
    return score_gnd(res.log, scn.to_dict())


@pytest.mark.parametrize("seed", [1, 7, 42, 100])
@pytest.mark.parametrize("band", ["calm", "standard", "heavy"])
def test_oracle_certifies(seed, band):
    s = _score(ScriptedGNDController, seed, band)
    assert s["gate"] == 1
    assert s["counts"]["arrived"] == s["counts"]["aircraft"]


def test_bad_controller_busts_across_seeds():
    busts = [_score(BadGNDController, s)["gate"] for s in range(1, 12)]
    assert all(g == 0 for g in busts)


@pytest.mark.parametrize("seed", [1, 7, 42])
def test_do_nothing_never_certifies(seed):
    # No-skill probe (X.5): pure inaction must bust on NEGLECT, never score.
    s = _score(DoNothingController, seed)
    assert s["gate"] == 0
    assert s["S"] == 0.0
    assert any(c["code"] == "NEGLECT" for c in s["cardinal_violations"])


class _TaxiOnlyController(ScriptedGNDController):
    """Low-skill probe (X.5): routes everyone, never clears the 31R crossing —
    stranding every departure at the hold bar must read as NEGLECT, not S≈0.8."""

    def step(self, observation):
        for ac in observation["aircraft"]:
            if not ac["route_assigned"]:
                return super().step(observation)
        return self.wait()


@pytest.mark.parametrize("seed", [1, 7, 42])
def test_strand_departures_never_certifies(seed):
    s = _score(_TaxiOnlyController, seed)
    assert s["gate"] == 0
    assert s["S"] == 0.0
    assert any("stranded" in c.get("detail", "") for c in s["cardinal_violations"])


def test_bad_controller_produces_cardinal_kinds():
    s = _score(BadGNDController, 42)
    kinds = {c["code"] for c in s["cardinal_violations"]}
    assert kinds & {"RI-CTRL", "DEADLOCK"}


def test_determinism_and_replay():
    scn = gnd_scenarios.generate(42)
    a = GroundSession(scn).run(ScriptedGNDController())
    b = GroundSession(gnd_scenarios.generate(42)).run(ScriptedGNDController())
    assert a.log.to_jsonl() == b.log.to_jsonl()
    replay = GroundSession(gnd_scenarios.generate(42)).run(
        ReplayAdapter([t["output"] for t in a.model_io]))
    assert replay.log.to_jsonl() == a.log.to_jsonl()


@pytest.mark.parametrize("seed", [1, 7, 42, 100, 2024])
def test_scenarios_feasible(seed):
    assert gnd_feasible(gnd_scenarios.generate(seed, band="standard"))


def test_generation_is_a_feasible_fixed_point():
    # generate() rejects-and-rerolls infeasible candidates (§12.2) and records the
    # seed actually used — regenerating from the run record reproduces the scenario.
    scn = gnd_scenarios.generate(42)
    assert gnd_scenarios.generate(scn.seed).to_dict() == scn.to_dict()


def test_ground_parser():
    pt = P.parse_ground_transmission(
        "Southwest 254, runway three one center, taxi via alpha, hold short runway three one right.",
        ["SWA254"])
    assert pt.acid == "SWA254"
    assert pt.intent == "taxi"
    assert pt.to_runway == "31C"
    assert pt.via == ["a"]
    assert pt.hold_short == ["31R"]

    px = P.parse_ground_transmission("Southwest 254, cross runway three one right.", ["SWA254"])
    assert px.intent == "crossing"
    assert px.cross == ["31R"]
