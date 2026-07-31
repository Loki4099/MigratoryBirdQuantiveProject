from __future__ import annotations

import argparse
import hashlib
import platform
import subprocess
import uuid
from pathlib import Path

from style_rotation.backtest.repository import BacktestRepository
from style_rotation.backtest.service import BacktestBatchService
from style_rotation.config.settings import get_settings
from style_rotation.persistence.session import create_postgres_engine, create_session_factory


def _uuid(value: str) -> uuid.UUID:
    return uuid.UUID(value)


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _code_hash() -> str:
    root = Path(__file__).resolve().parents[1] / "backtest"
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _git_commit() -> str:
    root = Path(__file__).resolve().parents[3]
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    dirty = subprocess.run(
        ["git", "status", "--porcelain"], cwd=root, text=True, capture_output=True, check=True
    ).stdout.strip()
    return f"{commit}-dirty" if dirty else commit


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run and publish formal v0.1 backtests")
    parser.add_argument("--data-version-id", type=_uuid)
    parser.add_argument("--cleaning-version-id", type=_uuid)
    parser.add_argument("--factor-version-id", type=_uuid)
    parser.add_argument("--strategy-version-id", type=_uuid)
    parser.add_argument("--variant-key", action="append")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    supplied = (
        args.data_version_id,
        args.cleaning_version_id,
        args.factor_version_id,
        args.strategy_version_id,
    )
    if any(value is None for value in supplied) and not all(value is None for value in supplied):
        raise SystemExit("Provide all four upstream version identifiers or none")
    settings = get_settings()
    engine = create_postgres_engine(settings.database_url)
    repository = BacktestRepository(create_session_factory(engine))
    if all(value is None for value in supplied):
        versions = repository.latest_signal_dataset_ids()
    else:
        if any(value is None for value in supplied):
            raise RuntimeError("Version identifiers unexpectedly missing")
        versions = supplied
    data_version_id, cleaning_version_id, factor_version_id, strategy_version_id = versions
    if any(
        value is None
        for value in (
            data_version_id,
            cleaning_version_id,
            factor_version_id,
            strategy_version_id,
        )
    ):
        raise RuntimeError("Version identifiers unexpectedly missing")
    code_hash = _code_hash()
    commit = _git_commit()
    lock_path = Path(__file__).resolve().parents[3] / "requirements.lock"
    engine_version_id = repository.ensure_engine_version(
        version_key=f"engine-v0.1.0-{commit[:8]}-{code_hash[:8]}",
        git_commit=commit,
        dependency_lock_hash=_file_hash(lock_path),
        code_hash=code_hash,
        python_version=platform.python_version(),
    )
    outcome = BacktestBatchService(repository).run(
        data_version_id=data_version_id,
        cleaning_version_id=cleaning_version_id,
        factor_version_id=factor_version_id,
        strategy_version_id=strategy_version_id,
        engine_version_id=engine_version_id,
        system_version=settings.system_version,
        variant_keys=set(args.variant_key) if args.variant_key else None,
    )
    print(
        f"experiment_id={outcome.experiment_id} completed_runs={outcome.completed_runs} "
        f"reused_runs={outcome.reused_runs}"
    )


if __name__ == "__main__":
    main()
