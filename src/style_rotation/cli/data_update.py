from __future__ import annotations

import argparse
import hashlib
from datetime import date
from pathlib import Path

from style_rotation.config.settings import get_settings
from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.data.cleaning import (
    MAX_ABSOLUTE_DAILY_RETURN,
    MAX_RATE_STALENESS_DAYS,
    REQUIRED_SYMBOLS,
)
from style_rotation.data.pipeline import DataIngestionService
from style_rotation.data.providers.fred import FredCsvProvider
from style_rotation.data.providers.yahoo import YahooFinanceProvider
from style_rotation.data.repository import DataRepository
from style_rotation.persistence.session import create_postgres_engine, create_session_factory


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def _code_hash() -> str:
    root = Path(__file__).resolve().parents[1] / "data"
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Download, validate, and publish a data version")
    parser.add_argument("--start", required=True, type=_parse_date)
    parser.add_argument("--end", required=True, type=_parse_date)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    settings = get_settings()
    engine = create_postgres_engine(settings.database_url)
    repository = DataRepository(create_session_factory(engine))
    service = DataIngestionService(
        repository,
        YahooFinanceProvider(settings.yahoo_timeout_seconds),
        FredCsvProvider(settings.fred_csv_url, settings.yahoo_timeout_seconds),
    )
    rules = {
        "required_symbols": REQUIRED_SYMBOLS,
        "adjustment": "adj_close_over_close_raw",
        "max_absolute_daily_return": str(MAX_ABSOLUTE_DAILY_RETURN),
        "max_rate_staleness_days": MAX_RATE_STALENESS_DAYS,
        "reserve_day_count": "ACT/365",
        "missing_market_values": "fail_publication",
    }
    cleaning_code_hash = _code_hash()
    outcome = service.run(
        start=args.start,
        end_inclusive=args.end,
        cleaning_version_key=f"cleaning-v0.1.0-{cleaning_code_hash[:8]}",
        cleaning_rules_hash=sha256_hexdigest(rules),
        cleaning_code_hash=cleaning_code_hash,
    )
    print(
        f"data_version_id={outcome.data_version_id} "
        f"cleaning_version_id={outcome.cleaning_version_id} "
        f"reused={outcome.reused} market_rows={outcome.market_rows} "
        f"rate_rows={outcome.rate_rows}"
    )


if __name__ == "__main__":
    main()
