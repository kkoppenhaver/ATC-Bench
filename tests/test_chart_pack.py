"""Seed-drawn chart constants (audit M6, P4.0d).

The public repo must not carry a memorizable answer key: departure frequency, SID
set, per-SID LOA altitudes, and the invalid-filing fallback are drawn per scenario
from the seeded airspace stream, and filing errors change the correct clearance.
"""

from __future__ import annotations

from atcbench.charts.kmrl_cd import CDChartPack
from atcbench.scenarios import cd as cd_scenarios

SEEDS = range(1, 9)


def _packs():
    return [cd_scenarios.generate(s, band="standard", session_seconds=3600).chart_pack
            for s in SEEDS]


def test_answers_vary_across_seeds():
    packs = _packs()
    assert len({p.departure_frequency for p in packs}) > 1
    assert len({frozenset(p.sids) for p in packs}) > 1
    altitudes = {s["initial_altitude"] for p in packs for s in p.sids.values()}
    assert len(altitudes) > 1  # the LOA is a table to look up, not a constant


def test_expected_clearances_follow_the_pack():
    for seed in SEEDS:
        scn = cd_scenarios.generate(seed, band="standard", session_seconds=3600)
        for exp in scn.expected_clearance.values():
            assert exp["route"] in scn.chart_pack.sids
            assert exp["altitude"] == scn.chart_pack.initial_altitude(exp["route"])
            assert exp["frequency"] == scn.chart_pack.departure_frequency


def test_invalid_sid_filing_changes_the_correct_answer():
    found = False
    for seed in range(1, 30):
        scn = cd_scenarios.generate(seed, band="heavy", session_seconds=3600)
        for fp in scn.flight_plans:
            if fp.filing_error == "invalid_sid":
                exp = scn.expected_clearance[fp.acid]
                assert fp.filed_sid not in scn.chart_pack.sids
                assert exp["route"] == scn.chart_pack.fallback_sid
                assert exp["altitude"] == scn.chart_pack.initial_altitude(exp["route"])
                found = True
        if found:
            break
    assert found, "no invalid_sid filing found in 30 heavy seeds"


def test_rb_freq_error_content_is_pack_relative():
    for seed in SEEDS:
        scn = cd_scenarios.generate(seed, band="heavy", session_seconds=3600)
        for err in scn.error_schedule.values():
            if err.code == "RB-FREQ":
                assert err.detail["wrong_frequency"] != scn.chart_pack.departure_frequency


def test_pack_round_trips_through_the_run_record():
    scn = cd_scenarios.generate(3, band="standard", session_seconds=3600)
    again = CDChartPack.from_dict(scn.chart_pack.to_dict())
    assert again == scn.chart_pack
    assert "chart_pack" in scn.to_dict()
