"""No-skill baseline regression suite (X.5; 2026-07 audit C1/C2).

The falsification contract, extending the oracle-certifies / bad-controller-busts
pattern: controllers with no ATC skill must never look competent. Every new position
MUST add its probes here — this suite is the Phase 3.5 exit test and grows with the
ladder exactly like the determinism suite (X.2) does.

Probes:
- DoNothingController — pure inaction. Must bust on NEGLECT everywhere.
- BlindCDCorrector — re-clears every readback without listening. Matched the oracle's
  S=1.0 before the audit; must score H=0 and sit far below the oracle. (Gate-level
  cert assertion arrives with P3.6.3's bust-rate certification.)
- TaxiOnlyGNDController — routes everyone, never clears the crossing. Scored 0.80
  while stranding every departure; must bust on stranded-NEGLECT.
"""

from __future__ import annotations

import pytest

from atcbench.harness.adapters import (
    BlindCDCorrector,
    DoNothingController,
    ScriptedCDController,
    TaxiOnlyGNDController,
)
from atcbench.harness.ground_session import GroundSession
from atcbench.harness.session import CDSession
from atcbench.harness.tower_session import TowerSession
from atcbench.scenarios import cd as cd_scenarios
from atcbench.scenarios import gnd as gnd_scenarios
from atcbench.scenarios import twr as twr_scenarios
from atcbench.scoring.cd import score_cd
from atcbench.scoring.gnd import score_gnd
from atcbench.scoring.twr import score_twr

SEEDS = (1, 7, 42)


def _cd(adapter_cls, seed):
    scn = cd_scenarios.generate(seed, band="standard", session_seconds=3600)
    return score_cd(CDSession(scn).run(adapter_cls()).log, scn.to_dict())


def _gnd(adapter_cls, seed):
    scn = gnd_scenarios.generate(seed)
    return score_gnd(GroundSession(scn).run(adapter_cls()).log, scn.to_dict())


def _twr(adapter_cls, seed):
    scn = twr_scenarios.generate(seed)
    return score_twr(TowerSession(scn).run(adapter_cls()).log, scn.to_dict())


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("runner", [_cd, _gnd, _twr], ids=["CD", "GND", "TWR"])
def test_do_nothing_never_certifies(runner, seed):
    s = runner(DoNothingController, seed)
    assert s["gate"] == 0
    assert s["S"] == 0.0
    assert any(c["code"] == "NEGLECT" for c in s["cardinal_violations"])


@pytest.mark.parametrize("seed", SEEDS + (100,))
def test_blind_corrector_scores_zero_hearback(seed):
    s = _cd(BlindCDCorrector, seed)
    assert s["components"]["H"] == 0.0
    assert s["counts"]["spurious_corrections"] >= 1
    assert s["S"] <= 0.80
    # And the oracle stays strictly above it — blind spam is never oracle-equal.
    assert _cd(ScriptedCDController, seed)["S"] > s["S"]


@pytest.mark.parametrize("seed", SEEDS)
def test_strand_departures_never_certifies(seed):
    s = _gnd(TaxiOnlyGNDController, seed)
    assert s["gate"] == 0
    assert s["S"] == 0.0
    assert any("stranded" in c.get("detail", "") for c in s["cardinal_violations"])
