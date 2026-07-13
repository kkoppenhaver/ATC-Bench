"""ATCBench command-line surface (DESIGN §17.1).

  atcbench run    --position CD|GND --seed N [--band standard] [--regime turn|metered|both]
                  --controller ... --out DIR
  atcbench score  DIR                          # re-score from the log (position auto-detected)
  atcbench replay DIR --out DIR2               # re-run recorded outputs; determinism check
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .harness import adapters as A
from .harness.ground_session import GroundSession
from .harness.regime import make_regime
from .harness.session import CDSession
from .harness.system_prompt import build_cd_system_prompt, prompt_hash
from .scenarios import cd as cd_scenarios
from .scenarios import gnd as gnd_scenarios
from .scoring.cd import score_cd
from .scoring.cd import score_run_dir as score_cd_dir
from .scoring.gnd import score_gnd
from .scoring.gnd import score_run_dir as score_gnd_dir

_CD_CONTROLLERS = {"scripted": A.ScriptedCDController, "bad": A.BadCDController}
_GND_CONTROLLERS = {"scripted": A.ScriptedGNDController, "bad": A.BadGNDController}


def _run_one(args: argparse.Namespace, regime_name: str):
    regime = make_regime(regime_name)
    if args.position == "CD":
        scn = cd_scenarios.generate(args.seed, band=args.band, session_seconds=args.session_seconds)
        ph = prompt_hash(build_cd_system_prompt(args.session_seconds, regime_name))
        session = CDSession(scn, prompt_hash=ph, regime=regime)
        result = session.run(_CD_CONTROLLERS[args.controller]())
        score = score_cd(result.log, scn.expected_clearance,
                         {a: e.to_dict() for a, e in scn.error_schedule.items()})
    else:  # GND
        scn = gnd_scenarios.generate(args.seed, band=args.band, session_seconds=args.session_seconds)
        session = GroundSession(scn, regime=regime)
        result = session.run(_GND_CONTROLLERS[args.controller]())
        score = score_gnd(result.log, scn.to_dict())
    score["regime"] = regime_name
    return result, score


def cmd_run(args: argparse.Namespace) -> int:
    regimes = ["turn", "metered"] if args.regime == "both" else [args.regime]
    scores: dict[str, dict] = {}
    for rn in regimes:
        result, score = _run_one(args, rn)
        scores[rn] = score
        if args.out:
            out = args.out if len(regimes) == 1 else f"{args.out.rstrip('/')}_{rn}"
            result.write(out)
            (Path(out) / "score.json").write_text(json.dumps(score, indent=2, sort_keys=True),
                                                  encoding="utf-8")
    if len(regimes) == 2:
        payload = {"turn": scores["turn"], "metered": scores["metered"],
                   "tempo_gap": round(scores["turn"]["S"] - scores["metered"]["S"], 4)}
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(json.dumps(scores[regimes[0]], indent=2, sort_keys=True))
    return 0


def _detect_position(run_dir: Path) -> str:
    scn = json.loads((run_dir / "scenario.json").read_text(encoding="utf-8"))
    return scn.get("position", "MRL_CD")


def cmd_score(args: argparse.Namespace) -> int:
    pos = _detect_position(Path(args.dir))
    score = score_gnd_dir(args.dir) if pos == "MRL_GND" else score_cd_dir(args.dir)
    print(json.dumps(score, indent=2, sort_keys=True))
    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    src = Path(args.dir)
    scn_dict = json.loads((src / "scenario.json").read_text(encoding="utf-8"))
    io = json.loads((src / "model_io.json").read_text(encoding="utf-8"))
    turns = [t["output"] for t in io["turns"]]
    regime = make_regime(io.get("regime", "turn"))
    if scn_dict.get("position") == "MRL_GND":
        scn = gnd_scenarios.generate(scn_dict["seed"], band=scn_dict["band"],
                                     session_seconds=scn_dict["session_seconds"])
        session = GroundSession(scn, prompt_hash=io.get("prompt_hash", "replay"), regime=regime)
    else:
        scn = cd_scenarios.generate(scn_dict["seed"], band=scn_dict["band"],
                                    session_seconds=scn_dict["session_seconds"])
        session = CDSession(scn, prompt_hash=io.get("prompt_hash", "replay"), regime=regime)
    result = session.run(A.ReplayAdapter(turns))
    result.write(args.out)
    identical = (src / "events.jsonl").read_text() == (Path(args.out) / "events.jsonl").read_text()
    print(f"replay written to {args.out}; events byte-identical to source: {identical}")
    return 0 if identical else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="atcbench")
    sub = parser.add_subparsers(dest="cmd", required=True)

    pr = sub.add_parser("run", help="run one session")
    pr.add_argument("--position", default="CD", choices=["CD", "GND"])
    pr.add_argument("--seed", type=int, required=True)
    pr.add_argument("--band", default="standard", choices=["calm", "standard", "heavy"])
    pr.add_argument("--regime", default="turn", choices=["turn", "metered", "both"])
    pr.add_argument("--controller", default="scripted", choices=["scripted", "bad"])
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
