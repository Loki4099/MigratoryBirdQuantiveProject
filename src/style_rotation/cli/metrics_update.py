from __future__ import annotations

import argparse
import hashlib
import platform
import subprocess
import uuid
from pathlib import Path

from style_rotation.config.settings import get_settings
from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.metrics.repository import MetricsRepository
from style_rotation.metrics.service import METRIC_CONFIGURATION, MetricComputationService
from style_rotation.persistence.session import create_postgres_engine, create_session_factory


def _uuid(value: str) -> uuid.UUID:
    return uuid.UUID(value)


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _code_hash() -> str:
    root = Path(__file__).resolve().parents[1] / "metrics"
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
    parser = argparse.ArgumentParser(
        description="Compute and publish formal v0.1 factor diagnostics and metrics"
    )
    parser.add_argument("--source-engine-version-id", type=_uuid)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    settings = get_settings()
    engine = create_postgres_engine(settings.database_url)
    repository = MetricsRepository(create_session_factory(engine))
    methodology_hash = sha256_hexdigest(METRIC_CONFIGURATION)
    code_hash = _code_hash()
    commit = _git_commit()
    lock_path = Path(__file__).resolve().parents[3] / "requirements.lock"
    metric_version_id = repository.ensure_metric_version(
        version_key=(f"metrics-v0.1.0-{commit[:8]}-{methodology_hash[:8]}-{code_hash[:8]}"),
        methodology_hash=methodology_hash,
        code_hash=code_hash,
        dependency_lock_hash=_file_hash(lock_path),
        git_commit=commit,
        python_version=platform.python_version(),
        configuration=METRIC_CONFIGURATION,
    )
    outcome = MetricComputationService(repository).run(
        metric_version_id=metric_version_id,
        methodology_hash=methodology_hash,
        source_engine_version_id=args.source_engine_version_id,
    )
    print(
        f"metric_version_id={outcome.metric_version_id} "
        f"diagnostic_sets_completed={outcome.diagnostic_sets_completed} "
        f"diagnostic_sets_reused={outcome.diagnostic_sets_reused} "
        f"publications_completed={outcome.publications_completed} "
        f"publications_reused={outcome.publications_reused}"
    )


if __name__ == "__main__":
    main()
