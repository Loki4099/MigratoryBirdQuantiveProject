from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import date


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the complete deterministic v0.1 pipeline")
    parser.add_argument("--start", required=True, type=_parse_date)
    parser.add_argument("--end", required=True, type=_parse_date)
    return parser


def build_commands(start: date, end: date) -> tuple[tuple[str, ...], ...]:
    python = sys.executable
    return (
        (python, "-m", "style_rotation.cli.data_update", "--start", str(start), "--end", str(end)),
        (python, "-m", "style_rotation.cli.factor_update"),
        (python, "-m", "style_rotation.cli.signal_update"),
        (python, "-m", "style_rotation.cli.backtest_update"),
        (python, "-m", "style_rotation.cli.metrics_update"),
    )


def main() -> None:
    args = build_parser().parse_args()
    if args.start >= args.end:
        raise SystemExit("--start must be earlier than --end")
    for command in build_commands(args.start, args.end):
        print(f"running={command[2]}", flush=True)
        subprocess.run(command, check=True)


if __name__ == "__main__":
    main()
