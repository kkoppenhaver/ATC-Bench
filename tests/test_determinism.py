"""Determinism contract (DESIGN §17.2): the environment is a pure function of
(seed, model_outputs). Two fresh runs match; replaying recorded outputs is
byte-identical to the original event log."""

from __future__ import annotations

from atcbench.harness.adapters import ReplayAdapter, ScriptedCDController
from atcbench.harness.session import CDSession
from atcbench.scenarios import cd as cd_scenarios


def _run(seed: int, band: str = "standard"):
    scn = cd_scenarios.generate(seed, band=band, session_seconds=3600)
    return CDSession(scn).run(ScriptedCDController())


def test_two_fresh_runs_byte_identical():
    a = _run(42)
    b = _run(42)
    assert a.log.to_jsonl() == b.log.to_jsonl()


def test_replay_byte_identical():
    src = _run(42)
    scn = cd_scenarios.generate(42, band="standard", session_seconds=3600)
    replayed = CDSession(scn).run(ReplayAdapter([t["output"] for t in src.model_io]))
    assert replayed.log.to_jsonl() == src.log.to_jsonl()


def test_different_seeds_differ():
    assert _run(1).log.to_jsonl() != _run(2).log.to_jsonl()


def test_cli_replay_verifies_all_artifacts(tmp_path):
    # §17.2 via the CLI: replay must reproduce every artifact, not just events.jsonl.
    from atcbench.cli import main

    src, out = tmp_path / "src", tmp_path / "replay"
    assert main(["run", "--position", "GND", "--seed", "7", "--out", str(src)]) == 0
    assert main(["replay", str(src), "--out", str(out)]) == 0
    for f in ("events.jsonl", "transcript.jsonl", "strips_history.jsonl", "score.json"):
        assert (src / f).read_text() == (out / f).read_text(), f
