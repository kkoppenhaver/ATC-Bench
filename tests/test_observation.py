"""Observation de-leakage (audit M1, DESIGN §11.2, §4.5).

The raw observation must never carry ground truth or pre-computed skill: no
filing-error hints, no pre-parsed readbacks, no future runway schedule, no derived
wake/occupancy bookkeeping. These tests capture real observations and pin the contract.
"""

from __future__ import annotations

from atcbench.harness.adapters import ModelAdapter
from atcbench.harness.ground_session import GroundSession
from atcbench.harness.session import CDSession
from atcbench.harness.tower_session import TowerSession
from atcbench.scenarios import cd as cd_scenarios
from atcbench.scenarios import gnd as gnd_scenarios
from atcbench.scenarios import twr as twr_scenarios


class _ObsRecorder(ModelAdapter):
    def __init__(self) -> None:
        self.observations: list[dict] = []

    def step(self, observation: dict) -> dict:
        self.observations.append(observation)
        return self.wait()


def test_cd_observation_carries_no_ground_truth():
    rec = _ObsRecorder()
    CDSession(cd_scenarios.generate(1, band="standard", session_seconds=3600)).run(rec)
    aircraft = [a for obs in rec.observations for a in obs["aircraft"]]
    assert aircraft
    for a in aircraft:
        assert "filing_error_hint" not in a["filed"]
        assert "last_readback" not in a


def test_gnd_observation_has_no_future_schedule():
    rec = _ObsRecorder()
    GroundSession(gnd_scenarios.generate(1)).run(rec)
    assert rec.observations
    for obs in rec.observations:
        for rw in obs["runways"].values():
            assert "next_hot_in" not in rw


def test_gnd_tower_coordination_is_on_frequency():
    rec = _ObsRecorder()
    res = GroundSession(gnd_scenarios.generate(1)).run(rec)
    coord = [t for t in res.transcript if t["from"] == "MRL_TWR"]
    assert any("hold all crossings" in t["text"] for t in coord)
    assert any("crossings at your discretion" in t["text"] for t in coord)


def test_twr_raw_observation_has_no_derived_runway_picture():
    rec = _ObsRecorder()
    TowerSession(twr_scenarios.generate(1)).run(rec)
    assert rec.observations
    for obs in rec.observations:
        assert "since_last_use_sec" not in obs["runway"]
        assert "last_use_wake" not in obs["runway"]


def test_twr_enriched_track_still_reports_derived_fields():
    rec = _ObsRecorder()
    TowerSession(twr_scenarios.generate(1), representation="enriched").run(rec)
    assert all("since_last_use_sec" in obs["runway"] for obs in rec.observations)
