from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .config import ConfigError, load_config
from .deck import build_study
from .postprocess import analyze_study
from .runner import resolve_executable, run_study


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="armor-impact",
        description="Build, run, and screen LS-DYNA torso/armor impact cases.",
    )
    parser.add_argument("command", choices=("doctor", "build", "run", "analyze", "all"))
    parser.add_argument("--config", default="study.toml", help="Path to the TOML study configuration")
    parser.add_argument("--study-dir", default="runs", help="Case output directory")
    parser.add_argument("--dry-run", action="store_true", help="Print solver commands without launching LS-DYNA")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        config = load_config(args.config)
    except (OSError, ConfigError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    if args.command == "doctor":
        executable = resolve_executable(config.solver.executable)
        print(f"Configuration: OK ({len(config.cases)} case(s))")
        print(f"Estimated torso solids per case: {config.mesh.body_nx * config.mesh.body_ny * config.mesh.body_nz}")
        print(f"LS-DYNA executable: {executable if executable else 'NOT FOUND'}")
        if executable is None:
            print(
                "Set LS_DYNA_EXECUTABLE in .env (or solver.executable in study.toml); "
                "build and dry-run still work."
            )
            return 1
        return 0

    if args.command in ("build", "all"):
        manifest = build_study(config, args.study_dir)
        print(f"Built {len(config.cases)} case(s): {manifest}")

    if args.command in ("run", "all"):
        try:
            results = run_study(args.study_dir, config.solver, dry_run=args.dry_run)
        except FileNotFoundError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        for result in results:
            print(f"{result.case_id}: {result.status} - {result.message}")
        if not args.dry_run and any(result.status in {"failed", "timeout", "blocked"} for result in results):
            return 3

    if args.command in ("analyze", "all") and not args.dry_run:
        try:
            summary = analyze_study(args.study_dir)
        except FileNotFoundError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        print(f"Summary: {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

