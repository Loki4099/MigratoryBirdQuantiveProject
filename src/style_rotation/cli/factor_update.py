from __future__ import annotations

import argparse
import hashlib
import uuid
from pathlib import Path

from style_rotation.config.settings import get_settings
from style_rotation.factors.repository import FactorRepository
from style_rotation.factors.service import FactorComputationService
from style_rotation.persistence.session import create_postgres_engine, create_session_factory


def _uuid(value: str) -> uuid.UUID:
    return uuid.UUID(value)


def _code_hash() -> str:
    root = Path(__file__).resolve().parents[1] / "factors"
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Calculate and publish the v0.1 factor pool")
    parser.add_argument("--data-version-id", type=_uuid)
    parser.add_argument("--cleaning-version-id", type=_uuid)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if (args.data_version_id is None) != (args.cleaning_version_id is None):
        raise SystemExit("Provide both version identifiers or neither")
    settings = get_settings()
    engine = create_postgres_engine(settings.database_url)
    repository = FactorRepository(create_session_factory(engine))
    if args.data_version_id is None:
        data_version_id, cleaning_version_id = repository.latest_clean_dataset_ids()
    else:
        data_version_id = args.data_version_id
        cleaning_version_id = args.cleaning_version_id
    factor_code_hash = _code_hash()
    outcome = FactorComputationService(repository).run(
        data_version_id=data_version_id,
        cleaning_version_id=cleaning_version_id,
        factor_version_key=f"factor-v0.1.0-{factor_code_hash[:8]}",
        factor_code_hash=factor_code_hash,
    )
    print(
        f"factor_version_id={outcome.factor_version_id} reused={outcome.reused} "
        f"factor_value_rows={outcome.factor_value_rows} "
        f"common_valid_start={outcome.common_valid_start.isoformat()}"
    )


if __name__ == "__main__":
    main()
