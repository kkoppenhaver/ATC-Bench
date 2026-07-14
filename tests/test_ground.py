"""Ground position: determinism, oracle certification, bad-controller busts, feasibility.

Mirrors the Phase 2 exit test (DESIGN §15): the oracle certifies at the standard band
and the scripted bad controller busts reliably.
"""

from __future__ import annotations

import pytest

from atcbench.baselines.feasibility import gnd_feasible
from atcbench.harness.adapters import BadGNDController, ReplayAdapter, ScriptedGNDController
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


def test_pilots_fly_the_transmitted_route():
    # P4.0b: the route is built from the *named* taxiways, not a canonical lookup.
    from atcbench.charts import kmrl_gnd as g

    assert g.build_route("G1", "HS_31C", ["alpha"]) == \
        ["G1", "A1", "A2", "HS_31R", "RW_31R", "A3", "HS_31C"]
    assert g.build_route("G1", "HS_31C", ["bravo"]) == []  # B doesn't reach 31C
    assert g.build_route("RWEX", "G1", ["bravo"]) == ["RWEX", "B2", "B1", "G1"]
    # Misroute flown as said: an arrival sent via A opposes the departure flow.
    assert g.build_route("RWEX", "G1", ["alpha"])[:3] == ["RWEX", "A3", "RW_31R"]
    # Unknown taxiways are dropped (misheard); the rest still resolves.
    assert g.build_route("G1", "HS_31C", ["alpha", "charlie"]) == \
        g.build_route("G1", "HS_31C", ["alpha"])
    assert g.build_route("G1", "HS_31C", ["charlie"]) == []


class _WrongHoldBarController(ScriptedGNDController):
    """Taxis the first departure to the WRONG hold bar (31R instead of 31C) and then
    leaves it there — the pilot must park at the end of the transmitted route and
    never count as arrived."""

    def __init__(self) -> None:
        super().__init__()
        self._sent = False

    def step(self, observation):
        if observation.get("channel_busy"):
            return self.wait()
        if not self._sent:
            dep = next((a for a in observation["aircraft"] if a["role"] == "departure"), None)
            if dep is not None:
                self._sent = True
                from atcbench.harness.adapters import _callsign_words, _runway_spoken
                return self.transmit(
                    f"{_callsign_words(dep['acid'])}, runway {_runway_spoken('31R')}, "
                    f"taxi via alpha.")
        return self.wait()


def test_wrong_destination_is_flown_and_never_arrives():
    scn = gnd_scenarios.generate(7)
    res = GroundSession(scn).run(_WrongHoldBarController())
    s = score_gnd(res.log, scn.to_dict())
    assert s["gate"] == 0  # everything else neglected; the misrouted one parks wrong
    cleared = {e.payload["acid"] for e in res.log.of_type("taxi_clearance")}
    arrived = {e.payload["acid"] for e in res.log.of_type("aircraft_arrived")}
    assert cleared and not (cleared & arrived)  # taxied to the wrong bar: not arrived


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
