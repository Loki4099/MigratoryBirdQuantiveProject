from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from style_rotation.v022.green_baseline_import import (  # noqa: E402
    write_green_baseline_import_plan,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--transfer", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--full-verify", action="store_true")
    args = parser.parse_args(argv)
    write_green_baseline_import_plan(
        args.transfer, args.output, full_verify=args.full_verify
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
