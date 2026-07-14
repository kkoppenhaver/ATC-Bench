"""Certification statistics (audit M3, DESIGN §13.4).

The 3/3-seeds gate let a 70%-bust-rate controller fully certify ~2.6% of attempts.
Certification is now estimation: Wilson-bounded bust rate over enough sessions, with
pass^k, clustered CIs, and ICC. These tests pin the math and the honest-small-n
property: 30 clean sessions cannot certify at the 5% threshold (upper bound ~11%).
"""

from __future__ import annotations

import json

import pytest

from atcbench.scoring.aggregate import (
    CERT_BUST_UPPER,
    aggregate,
    icc_oneway,
    wilson_upper,
)


def test_wilson_upper_bounds():
    assert wilson_upper(0, 0) == 1.0
    u30 = wilson_upper(0, 30)
    assert 0.10 < u30 < 0.13  # zero busts in 30 is still ~11% worst-case
    assert wilson_upper(0, 100) < u30
    assert wilson_upper(3, 30) > u30
    assert wilson_upper(30, 30) == 1.0


def test_small_n_cannot_certify_even_when_clean():
    runs = [{"seed": s, "trial": 0, "gate": 1, "S": 1.0} for s in range(30)]
    agg = aggregate(runs)
    assert agg["bust_rate"] == 0.0
    assert not agg["certified"]
    assert str(CERT_BUST_UPPER) in agg["certification_note"]


def test_enough_clean_sessions_certify():
    runs = [{"seed": s, "trial": 0, "gate": 1, "S": 0.95} for s in range(80)]
    agg = aggregate(runs)
    assert agg["bust_wilson_upper_95"] < CERT_BUST_UPPER
    assert agg["certified"]


def test_audit_bust_rate_controller_never_close_to_certifying():
    # The audit's BadCD-class policy busts ~70% of sessions; under 3/3-seeds it
    # certified 1-in-38 attempts. Here its upper bound is nowhere near 5%.
    runs = [{"seed": s, "trial": 0, "gate": 1 if s % 10 < 3 else 0, "S": 0.2}
            for s in range(100)]
    agg = aggregate(runs)
    assert agg["bust_rate"] == 0.7
    assert agg["bust_wilson_upper_95"] > 0.6
    assert not agg["certified"]


def test_icc_consistent_and_inconsistent():
    assert icc_oneway([[0.9, 0.9], [0.5, 0.5]]) == 1.0
    noisy = icc_oneway([[0.0, 1.0], [1.0, 0.0], [0.0, 1.0]])
    assert noisy is not None and noisy < 0.0  # within-seed noise dominates
    assert icc_oneway([[0.5], [0.7]]) is None  # single trials: no ICC


def test_pass_all_trials_is_stricter_than_pass_at_1():
    runs = []
    for s in range(10):
        runs.append({"seed": s, "trial": 0, "gate": 1, "S": 0.9})
        runs.append({"seed": s, "trial": 1, "gate": 1 if s < 5 else 0, "S": 0.9})
    agg = aggregate(runs)
    assert agg["pass_at_1"] == 0.75
    assert agg["pass_all_trials"] == 0.5


def test_cli_evaluate_end_to_end(tmp_path, capsys):
    from atcbench.cli import main

    rc = main(["evaluate", "--position", "CD", "--n-seeds", "4", "--trials", "2",
               "--controller", "scripted", "--out", str(tmp_path / "ev")])
    assert rc == 0
    summary = json.loads((tmp_path / "ev" / "summary.json").read_text())
    assert summary["sessions"] == 8 and summary["seeds"] == 4
    assert summary["bust_rate"] == 0.0
    assert summary["mean_S"] == 1.0
    assert summary["icc_trials"] == 1.0  # scripted controller: trials identical
    assert not summary["certified"]  # 8 sessions can never certify
    assert (tmp_path / "ev" / "seed1_t0" / "score.json").exists()


def test_cli_evaluate_bad_controller_busts(capsys):
    from atcbench.cli import main

    rc = main(["evaluate", "--position", "GND", "--n-seeds", "3", "--trials", "1",
               "--controller", "bad"])
    assert rc == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["bust_rate"] == 1.0
    assert summary["bust_wilson_upper_95"] == pytest.approx(1.0)
    assert not summary["certified"]