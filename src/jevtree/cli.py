from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .blocksworld import parse_problem
from .game24 import Game24Problem
from .probability_tree import enumerate_probability_tree
from .providers import HeuristicBackend, TypeSafeBackend
from .search import SearchConfig, search
from .serialization import dumps


def _read_key(path: str | None, env_name: str) -> str:
    if path:
        return Path(path).read_text(encoding="utf-8").strip()
    value = os.environ.get(env_name, "").strip()
    if not value:
        raise SystemExit(f"set {env_name} or pass the key-file option")
    return value


def _search_command(args: argparse.Namespace) -> int:
    problem = parse_problem(Path(args.problem))
    if args.provider == "typesafe":
        backend = TypeSafeBackend(api_key=_read_key(args.typesafe_key_file, "TYPESAFE_API_KEY"), model=args.model)
    else:
        backend = HeuristicBackend()
    config = SearchConfig(
        max_depth=args.max_depth,
        beam_width=args.beam_width,
        min_width=min(args.min_width, args.beam_width),
        mass_coverage=args.mass_coverage,
        heuristic_weight=args.heuristic_weight,
        probability_weight=args.probability_weight,
        adaptive=not args.fixed_width,
    )
    result = search(problem, backend, config)
    output = dumps(result)
    if args.output:
        Path(args.output).write_text(output + "\n", encoding="utf-8")
    else:
        print(output)
    return 0 if result.solved else 2


def _game24_command(args: argparse.Namespace) -> int:
    if args.provider == "typesafe":
        backend = TypeSafeBackend(api_key=_read_key(args.typesafe_key_file, "TYPESAFE_API_KEY"), model=args.model)
    else:
        backend = HeuristicBackend()
    try:
        problem = Game24Problem(args.numbers, target=args.target)
        result = enumerate_probability_tree(
            problem,
            backend,
            max_depth=len(args.numbers) - 1,
            question_batch_size=args.question_batch_size,
            exploration_epsilon=args.exploration_epsilon,
            include_edges=args.include_edges,
        )
        output = dumps(result)
        if args.output:
            Path(args.output).write_text(output + "\n", encoding="utf-8")
        else:
            print(output)
        return 0 if result.outcome_probability.get("success", 0.0) > 0.0 else 2
    finally:
        close = getattr(backend, "close", None)
        if close is not None:
            close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jev-tree")
    subparsers = parser.add_subparsers(dest="command", required=True)
    solve_parser = subparsers.add_parser("solve", help="run greedy or graph search on one Blocksworld PDDL problem")
    solve_parser.add_argument("problem")
    solve_parser.add_argument("--provider", choices=("heuristic", "typesafe"), default="heuristic")
    solve_parser.add_argument("--model", default="jev-latest")
    solve_parser.add_argument("--typesafe-key-file")
    solve_parser.add_argument("--max-depth", type=int, default=48)
    solve_parser.add_argument("--beam-width", type=int, default=8)
    solve_parser.add_argument("--min-width", type=int, default=2)
    solve_parser.add_argument("--mass-coverage", type=float, default=0.98)
    solve_parser.add_argument("--heuristic-weight", type=float, default=1.25)
    solve_parser.add_argument("--probability-weight", type=float, default=1.0)
    solve_parser.add_argument("--fixed-width", action="store_true")
    solve_parser.add_argument("--output")
    solve_parser.set_defaults(func=_search_command)

    game24_parser = subparsers.add_parser(
        "game24",
        help="enumerate the complete canonical Game-of-24 leaf distribution",
    )
    game24_parser.add_argument("numbers", nargs="+", type=int)
    game24_parser.add_argument("--target", type=int, default=24)
    game24_parser.add_argument("--provider", choices=("heuristic", "typesafe"), default="heuristic")
    game24_parser.add_argument("--model", default="jev-latest")
    game24_parser.add_argument("--typesafe-key-file")
    game24_parser.add_argument("--question-batch-size", type=int, default=128)
    game24_parser.add_argument(
        "--exploration-epsilon",
        type=float,
        default=0.0,
        help="mix this much uniform action mass into Jev probabilities; reported separately from raw Jev mass",
    )
    game24_parser.add_argument("--include-edges", action="store_true")
    game24_parser.add_argument("--output")
    game24_parser.set_defaults(func=_game24_command)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
