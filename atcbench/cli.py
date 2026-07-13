"""ATCBench command-line surface (DESIGN §17.1).

Implements the CD slice of:
  atcbench run    --position CD --seed N [--band standard] [--controller scripted|bad|anthropic] --out DIR
  atcbench score  DIR
  atcbench replay DIR --out DIR2      # re-run recorded model outputs; determinism check
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .harness.adapters import BadCDController, ReplayAdapter, ScriptedCDController
from .harness.session import CDSession
from .harness.system_prompt import build_cd_system_prompt, prompt_hash
from .scenarios import cd as cd_scenarios
from .scoring.cd import score_cd, score_run_dir


def _make_adapter(name: str, scenario, session_seconds: int):
    if name == "scripted":
        return ScriptedCDController()
    if name == "bad":
        return BadCDController()
    if name == "anthropic":  # pragma: no cover - requires key
        from .harness.adapters import AnthropicAdapter
        from .harness.tools import CD_TOOLS

        sp = build_cd_system_prompt(session_seconds)
        return AnthropicAdapter(model_id="claude-opus-4-8", system_prompt=sp, tools=CD_TOOLS)
    raise SystemExit(f"unknown controller {name!r}")


def cmd_run(args: argparse.Namespace) -> int:
    scn = cd_scenarios.generate(args.seed, band=args.band, session_seconds=args.session_seconds)
    sp = build_cd_system_prompt(args.session_seconds)
    ph = prompt_hash(sp)
    session = CDSession(scn, prompt_hash=ph)
    adapter = _make_adapter(args.controller, scn, args.session_seconds)
    result = session.run(adapter)
    score = score_cd(result.log, scn.expected_clearance, {a: e.to_dict() for a, e in scn.error_schedule.items()})
    if args.out:
        result.write(args.out)
        (Path(args.out) / "score.json").write_text(json.dumps(score, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(score, indent=2, sort_keys=True))
    return 0


def cmd_score(args: argparse.Namespace) -> int:
    print(json.dumps(score_run_dir(args.dir), indent=2, sort_keys=True))
    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    src = Path(args.dir)
    scn_dict = json.loads((src / "scenario.json").read_text(encoding="utf-8"))
    io = json.loads((src / "model_io.json").read_text(encoding="utf-8"))
    scn = cd_scenarios.generate(
        scn_dict["seed"], band=scn_dict["band"], session_seconds=scn_dict["session_seconds"]
    )
    adapter = ReplayAdapter([t["output"] for t in io["turns"]])
    session = CDSession(scn, prompt_hash=io.get("prompt_hash", "replay"))
    result = session.run(adapter)
    result.write(args.out)
    # Determinism check against the source log, if present.
    src_log = (src / "events.jsonl").read_text(encoding="utf-8")
    new_log = (Path(args.out) / "events.jsonl").read_text(encoding="utf-8")
    identical = src_log == new_log
    print(f"replay written to {args.out}; events byte-identical to source: {identical}")
    return 0 if identical else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="atcbench")
    sub = parser.add_subparsers(dest="cmd", required=True)

    pr = sub.add_parser("run", help="run one session")
    pr.add_argument("--position", default="CD", choices=["CD"])
    pr.add_argument("--seed", type=int, required=True)
    pr.add_argument("--band", default="standard", choices=list(cd_scenarios.BANDS))
    pr.add_argument("--controller", default="scripted", choices=["scripted", "bad", "anthropic"])
    pr.add_argument("--session-seconds", type=int, default=3600)
    pr.add_argument("--out", default=None)
    pr.set_defaults(func=cmd_run)

    ps = sub.add_parser("score", help="re-score a run directory")
    ps.add_argument("dir")
    ps.set_defaults(func=cmd_score)

    prp = sub.add_parser("replay", help="replay recorded model outputs; check determinism")
    prp.add_argument("dir")
    prp.add_argument("--out", required=True)
    prp.set_defaults(func=cmd_replay)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
