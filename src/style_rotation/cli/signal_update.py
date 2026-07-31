from __future__ import annotations

import argparse
import uuid

from style_rotation.config.settings import get_settings
from style_rotation.persistence.session import create_postgres_engine, create_session_factory
from style_rotation.signals.repository import SignalRepository
from style_rotation.signals.service import STRATEGY_CONFIGURATION_HASH, SignalComputationService


def _uuid(value: str) -> uuid.UUID:
    return uuid.UUID(value)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate and publish v0.1 target positions")
    parser.add_argument("--data-version-id", type=_uuid)
    parser.add_argument("--cleaning-version-id", type=_uuid)
    parser.add_argument("--factor-version-id", type=_uuid)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    supplied = (
        args.data_version_id,
        args.cleaning_version_id,
        args.factor_version_id,
    )
    if any(value is None for value in supplied) and not all(value is None for value in supplied):
        raise SystemExit("Provide all three version identifiers or none")
    settings = get_settings()
    engine = create_postgres_engine(settings.database_url)
    repository = SignalRepository(create_session_factory(engine))
    if all(value is None for value in supplied):
        data_version_id, cleaning_version_id, factor_version_id = (
            repository.latest_factor_dataset_ids()
        )
    else:
        data_version_id = args.data_version_id
        cleaning_version_id = args.cleaning_version_id
        factor_version_id = args.factor_version_id
    if data_version_id is None or cleaning_version_id is None or factor_version_id is None:
        raise RuntimeError("Version identifiers unexpectedly missing")
    outcome = SignalComputationService(repository).run(
        data_version_id=data_version_id,
        cleaning_version_id=cleaning_version_id,
        factor_version_id=factor_version_id,
        strategy_version_key=f"strategy-v0.1.0-{STRATEGY_CONFIGURATION_HASH[:8]}",
    )
    print(
        f"strategy_version_id={outcome.strategy_version_id} reused={outcome.reused} "
        f"events={outcome.event_count} positions={outcome.position_count} "
        f"first_signal_date={outcome.first_signal_date.isoformat()}"
    )


if __name__ == "__main__":
    main()
