from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock

import pytest
from sqlalchemy import Engine

from style_rotation.cli.v022_targeted_yahoo_market_repair import repair_spec
from style_rotation.data.providers.snapshots import RawFetch
from style_rotation.lineage.service import PublicationResult
from style_rotation.v022.market_reconciliation import (
    AlternateObservationSetPublication,
    MarketGapResolutionPublication,
    MarketGapResolutionSpec,
)
from style_rotation.v022.targeted_yahoo_market_repair import (
    PRIMARY_V3_DATASET_PUBLICATION_ID,
    TargetedYahooMarketRepairService,
    TargetedYahooProviderIdentity,
    TargetedYahooRepairEntry,
    TargetedYahooRepairRepository,
    TargetedYahooRepairSpec,
    validate_targeted_yahoo_payload,
)


def test_entry_requires_sorted_exact_sessions_inside_reviewed_interval() -> None:
    with pytest.raises(ValueError, match="unique and sorted"):
        _entry(expected_sessions=(date(2020, 1, 3), date(2020, 1, 2)))
    with pytest.raises(ValueError, match="inside"):
        _entry(
            gap_start=date(2020, 1, 3),
            gap_end=date(2020, 1, 3),
            expected_sessions=(date(2020, 1, 2),),
        )


def test_payload_validation_accepts_only_exact_positive_valid_rows() -> None:
    entry = _entry()

    result = validate_targeted_yahoo_payload(entry, _fetch(_valid_payload()))

    assert result.sessions == (date(2020, 1, 2), date(2020, 1, 3))
    assert result.row_count == 2
    assert len(result.payload_sha256) == 64


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        (
            ("2020-01-02,10,11,9,10,10,100,0,0",),
            "exactly match",
        ),
        (
            (
                "2020-01-02,10,11,9,10,10,100,0,0",
                "2020-01-03,10,11,9,10,10,0,0,0",
            ),
            "positive integer",
        ),
        (
            (
                "2020-01-02,10,9,8,10,10,100,0,0",
                "2020-01-03,10,11,9,10,10,100,0,0",
            ),
            "high is below",
        ),
        (
            (
                "2020-01-02,10,11,9,0,10,100,0,0",
                "2020-01-03,10,11,9,10,10,100,0,0",
            ),
            "must be positive",
        ),
    ],
)
def test_payload_validation_fails_closed_on_incomplete_or_invalid_bars(
    rows: tuple[str, ...], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        validate_targeted_yahoo_payload(_entry(), _fetch(_payload(*rows)))


def test_payload_validation_rejects_provider_identity_mismatch() -> None:
    fetched = _fetch(_valid_payload())
    fetched.request_parameters["tickers"] = "OTHER"

    with pytest.raises(ValueError, match="identity"):
        validate_targeted_yahoo_payload(_entry(), fetched)


def test_cli_document_parses_explicit_reviewed_entry() -> None:
    spec = repair_spec(
        {
            "created_by": "data-reviewer",
            "entries": [
                {
                    "security_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                    "provider_symbol": "TEST",
                    "gap_key": "test_missing_2020_01",
                    "gap_type": "missing_bar",
                    "gap_start": "2020-01-02",
                    "gap_end": "2020-01-03",
                    "expected_sessions": ["2020-01-02", "2020-01-03"],
                    "reason": "Reviewed isolated provider gap",
                }
            ],
        }
    )

    assert spec.primary_dataset_publication_id == PRIMARY_V3_DATASET_PUBLICATION_ID
    assert spec.entries[0].expected_sessions == (
        date(2020, 1, 2),
        date(2020, 1, 3),
    )


def test_service_publishes_snapshot_subject_observation_review_and_resolution() -> None:
    entry = _entry()
    identity = _identity(entry)
    repository = cast(TargetedYahooRepairRepository, Mock())
    repository.resolve_identity.return_value = identity
    source_snapshot_id = uuid.uuid4()
    repository.source_snapshot_id.return_value = source_snapshot_id
    adapter = Mock()
    adapter.fetch.return_value = _fetch(_valid_payload())
    snapshot = _publication()
    snapshots = Mock()
    snapshots.publish.return_value = snapshot
    subject_id = uuid.uuid4()
    subjects = Mock()
    subjects.bind.return_value = SimpleNamespace(
        source_snapshot_security_subject_id=subject_id,
        reused=False,
    )
    observation = AlternateObservationSetPublication(
        uuid.uuid4(), uuid.uuid4(), "a" * 64, 2, 0, False
    )
    observations = Mock()
    observations.publish.return_value = observation
    review = _publication()
    artifacts = Mock()
    artifacts.publish.return_value = review
    resolution = MarketGapResolutionPublication(uuid.uuid4(), uuid.uuid4(), "b" * 64, False)
    resolutions = Mock()
    resolutions.publish.return_value = resolution
    service = TargetedYahooMarketRepairService(
        cast(Engine, object()),
        adapter,
        repository=repository,
        snapshots=snapshots,
        subjects=subjects,
        observations=observations,
        artifacts=artifacts,
        resolutions=resolutions,
    )

    published = service.publish(
        TargetedYahooRepairSpec(
            PRIMARY_V3_DATASET_PUBLICATION_ID,
            (entry,),
            "data-reviewer",
        )
    )

    repository.validate_primary_v3.assert_called_once_with(PRIMARY_V3_DATASET_PUBLICATION_ID)
    adapter.fetch.assert_called_once_with("TEST", date(2020, 1, 2), date(2020, 1, 4))
    subjects.bind.assert_called_once_with(
        source_snapshot_id=source_snapshot_id,
        security_id=entry.security_id,
        security_identifier_id=identity.security_identifier_id,
        fetch_status="fetched",
    )
    resolution_spec = cast(MarketGapResolutionSpec, resolutions.publish.call_args.args[0])
    assert resolution_spec.resolution_kind == "replace_with_alternate"
    assert resolution_spec.alternate_observation_set_id == (
        observation.alternate_observation_set_id
    )
    assert resolution_spec.evidence[0].artifact_id == review.artifact_id
    assert published[0].market_gap_resolution_id == resolution.market_gap_resolution_id


def test_service_validates_entire_batch_before_publishing_any_snapshot() -> None:
    first = _entry()
    second = _entry(
        security_id=uuid.uuid4(),
        provider_symbol="TEST2",
        gap_key="test2_missing",
    )
    repository = cast(TargetedYahooRepairRepository, Mock())
    repository.resolve_identity.side_effect = (_identity(first), _identity(second))
    adapter = Mock()
    adapter.fetch.side_effect = (
        _fetch(_valid_payload()),
        _fetch(
            _payload(
                "2020-01-02,10,11,9,10,10,100,0,0",
                "2020-01-03,10,11,9,10,10,0,0,0",
            ),
            symbol="TEST2",
        ),
    )
    snapshots = Mock()
    service = TargetedYahooMarketRepairService(
        cast(Engine, object()),
        adapter,
        repository=repository,
        snapshots=snapshots,
        subjects=Mock(),
        observations=Mock(),
        artifacts=Mock(),
        resolutions=Mock(),
    )

    with pytest.raises(
        ValueError,
        match=(
            "Targeted Yahoo repair test2_missing failed validation: "
            "Invalid targeted Yahoo repair row 3: volume must be a positive integer"
        ),
    ):
        service.publish(
            TargetedYahooRepairSpec(
                PRIMARY_V3_DATASET_PUBLICATION_ID,
                (first, second),
                "data-reviewer",
            )
        )

    snapshots.publish.assert_not_called()


def _entry(**overrides: Any) -> TargetedYahooRepairEntry:
    values: dict[str, Any] = {
        "security_id": uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        "provider_symbol": "TEST",
        "gap_key": "test_missing_2020_01",
        "gap_type": "missing_bar",
        "gap_start": date(2020, 1, 2),
        "gap_end": date(2020, 1, 3),
        "expected_sessions": (date(2020, 1, 2), date(2020, 1, 3)),
        "reason": "Reviewed isolated provider gap",
    }
    values.update(overrides)
    return TargetedYahooRepairEntry(**values)


def _identity(entry: TargetedYahooRepairEntry) -> TargetedYahooProviderIdentity:
    return TargetedYahooProviderIdentity(
        uuid.uuid4(),
        entry.security_id,
        entry.provider_symbol,
        None,
        None,
    )


def _valid_payload() -> bytes:
    return _payload(
        "2020-01-02,10,11,9,10,10,100,0,0",
        "2020-01-03,11,12,10,11,11,200,0.1,0",
    )


def _payload(*rows: str) -> bytes:
    return (
        "session_date,Open,High,Low,Close,Adj Close,Volume,Dividends,Stock Splits\n"
        + "\n".join(rows)
        + "\n"
    ).encode("utf-8")


def _fetch(payload: bytes, *, symbol: str = "TEST") -> RawFetch:
    requested = datetime(2026, 8, 20, 1, 0, tzinfo=UTC)
    fetched = datetime(2026, 8, 20, 1, 0, 1, tzinfo=UTC)
    return RawFetch(
        requested_at=requested,
        fetched_at=fetched,
        as_of_at=fetched,
        media_type="text/csv; charset=utf-8",
        request_parameters={"tickers": symbol, "provider_ticker": symbol},
        response_metadata={"adapter": "unit-test"},
        payload=payload,
    )


def _publication() -> PublicationResult:
    return PublicationResult(
        uuid.uuid4(),
        "1" * 64,
        "2" * 64,
        "3" * 64,
        False,
    )
