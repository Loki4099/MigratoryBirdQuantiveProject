from __future__ import annotations

import argparse
from pathlib import Path

from style_rotation.compatibility.v021_baseline import (
    build_v021_baseline,
    verify_baseline,
    write_baseline,
)
from style_rotation.config.settings import get_settings
from style_rotation.persistence.session import create_postgres_engine


def main() -> int:
    parser = argparse.ArgumentParser(description="Freeze or verify the v0.21 M0 oracle manifest")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("v0.22/m0/v021-baseline-manifest.v0.22.0.json"),
    )
    parser.add_argument(
        "--source-commit",
        default="85a600811b2f58a7bb4be13b2a9c707035891d98",
    )
    parser.add_argument("--contract-tag", default="v0.22.0-contract")
    parser.add_argument("--frozen-at", default="2026-08-10T16:29:50+08:00")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()

    engine = create_postgres_engine(get_settings().database_url)
    try:
        payload = build_v021_baseline(
            engine,
            args.repo_root,
            source_commit=args.source_commit,
            contract_tag=args.contract_tag,
            frozen_at=args.frozen_at,
        )
    finally:
        engine.dispose()

    output = args.output if args.output.is_absolute() else args.repo_root / args.output
    if args.verify:
        verify_baseline(output, payload)
        print(f"verified {output}")
    else:
        write_baseline(output, payload)
        print(f"wrote {output}")
    print(f"payload_sha256={payload['payload_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
