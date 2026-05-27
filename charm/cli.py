from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from .adapters import make_adapter, resolve_model
from .dataset import read_manifest
from .eval import run_eval, summarize_run


def parse_max_attempts(value: str) -> int | None:
    if value.lower() in {"unlimited", "inf", "none"}:
        return None
    attempts = int(value)
    if attempts < 1:
        raise argparse.ArgumentTypeError("max attempts must be >= 1 or unlimited")
    return attempts


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="charm")
    subparsers = parser.add_subparsers(dest="command", required=True)

    eval_parser = subparsers.add_parser("eval")
    eval_parser.add_argument("--manifest", type=Path)
    eval_parser.add_argument(
        "--provider",
        choices=["openai", "anthropic", "google"],
    )
    eval_parser.add_argument("--model")
    eval_parser.add_argument("--max-attempts", type=parse_max_attempts)
    eval_parser.add_argument("--concurrency", type=int)
    eval_parser.add_argument("--limit", type=int)
    eval_parser.add_argument("--out", type=Path)

    return parser


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "eval":
        provider = args.provider or os.environ.get("CHARM_PROVIDER", "openai")
        manifest = args.manifest or (
            Path(os.environ["CHARM_MANIFEST"]) if os.environ.get("CHARM_MANIFEST") else None
        )
        if not manifest:
            manifest = Path("data/charm-bench-100.jsonl")
        limit_env = os.environ.get("CHARM_LIMIT")
        if args.limit is None:
            limit = int(limit_env) if limit_env else None
        else:
            limit = args.limit
        if limit is not None and limit < 1:
            raise ValueError("limit must be >= 1")
        concurrency_env = os.environ.get("CHARM_CONCURRENCY")
        if args.concurrency is None:
            concurrency = int(concurrency_env) if concurrency_env else 1
        else:
            concurrency = args.concurrency
        if concurrency < 1:
            raise ValueError("concurrency must be >= 1")

        max_attempts_env = os.environ.get("CHARM_MAX_ATTEMPTS")
        if args.max_attempts is None:
            max_attempts = parse_max_attempts(max_attempts_env) if max_attempts_env else 1
        else:
            max_attempts = args.max_attempts

        model_name = resolve_model(provider, args.model) or ""
        if not args.out:
            normalized = normalize_model_name(model_name or provider)
            args.out = Path("runs") / normalized / "run.jsonl"
        adapter = make_adapter(provider, model_name or args.model)
        problems = list(read_manifest(manifest))
        if limit:
            problems = problems[:limit]
        stats = run_eval(
            problems,
            adapter,
            max_attempts,
            args.out,
            concurrency=concurrency,
        )
        skipped = stats.get("skipped", 0)
        print(
            f"evaluated {stats['count']} problems to {args.out} "
            f"(success_rate={stats['success_rate']:.2%}, skipped={skipped})"
        )
        print(json.dumps(summarize_run(args.out), ensure_ascii=False, indent=2))
        return 0

    parser.error(f"unknown command: {args.command}")
    return 2


def normalize_model_name(value: str) -> str:
    normalized = value.strip().replace("/", "-").replace("\\", "-").replace(" ", "-")
    return normalized or "unknown"


if __name__ == "__main__":
    raise SystemExit(main())
