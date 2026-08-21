from __future__ import annotations

import csv
import hashlib
import io
import uuid
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, Protocol, cast

from sqlalchemy import Engine, text

from style_rotation.data.providers.snapshots import RawFetch, snapshot_key
from style_rotation.data.service import SourceSnapshotService
from style_rotation.lineage.service import ArtifactService, DependencyInput, PublicationResult
from style_rotation.v022.data_seed_import import (
    SourceSnapshotSecuritySubjectPublication,
    SourceSnapshotSecuritySubjectService,
)
from style_rotation.v022.market_reconciliation import (
    AlternateObservationService,
    AlternateObservationSetPublication,
    AlternateObservationSetSpec,
    GapResolutionEvidenceRef,
    MarketGapResolutionPublication,
    MarketGapResolutionService,
    MarketGapResolutionSpec,
)

PRIMARY_V3_DATASET_PUBLICATION_ID = uuid.UUID("22753730-d9cd-539d-bb04-b9ce72da6e93")
PRIMARY_V3_DATASET_KEY = "us_sp500_historical_daily_free_research_v1"

_PROVIDER_SCOPE = "yahoo_yfinance"
_SERIES_KEY = "us_equity_daily_market_yahoo"
_SERIES_VERSION = 1
_REVIEW_CONTRACT = "v0.22.targeted_yahoo_market_repair_review.v1"
_VALIDATION_CONTRACT = "v0.22.targeted_yahoo_market_repair_validation.v1"

RepairGapType = Literal["missing_bar", "provider_conflict"]


class MarketSnapshotAdapter(Protocol):
    def fetch(self, symbol: str, start: date, end_exclusive: date) -> RawFetch: ...


class _SnapshotPublisher(Protocol):
    def publish(self, item: object) -> PublicationResult: ...


class _SubjectPublisher(Protocol):
    def bind(
        self,
        *,
        source_snapshot_id: uuid.UUID,
        security_id: uuid.UUID,
        security_identifier_id: uuid.UUID,
        fetch_status: str,
        failure_reason: str | None = None,
    ) -> SourceSnapshotSecuritySubjectPublication: ...


class _ObservationPublisher(Protocol):
    def publish(self, spec: AlternateObservationSetSpec) -> AlternateObservationSetPublication: ...


class _ResolutionPublisher(Protocol):
    def publish(self, spec: MarketGapResolutionSpec) -> MarketGapResolutionPublication: ...


class _ArtifactPublisher(Protocol):
    def publish(self, **kwargs: Any) -> PublicationResult: ...


@dataclass(frozen=True, slots=True)
class TargetedYahooRepairEntry:
    security_id: uuid.UUID
    provider_symbol: str
    gap_key: str
    gap_type: RepairGapType
    gap_start: date
    gap_end: date
    expected_sessions: tuple[date, ...]
    reason: str
    version_number: int = 1

    def __post_init__(self) -> None:
        for label, value in (
            ("provider_symbol", self.provider_symbol),
            ("gap_key", self.gap_key),
            ("reason", self.reason),
        ):
            if not value.strip() or value.strip() != value:
                raise ValueError(f"{label} must be non-empty normalized text")
        if self.gap_type not in ("missing_bar", "provider_conflict"):
            raise ValueError("Targeted Yahoo repair gap_type is unsupported")
        if self.gap_start > self.gap_end:
            raise ValueError("Targeted Yahoo repair interval is reversed")
        if self.version_number < 1:
            raise ValueError("Targeted Yahoo repair version_number must be positive")
        if not self.expected_sessions:
            raise ValueError("Targeted Yahoo repair requires explicit expected sessions")
        if tuple(sorted(set(self.expected_sessions))) != self.expected_sessions:
            raise ValueError("Expected sessions must be unique and sorted")
        if any(item < self.gap_start or item > self.gap_end for item in self.expected_sessions):
            raise ValueError("Expected sessions must fall inside the reviewed interval")


@dataclass(frozen=True, slots=True)
class TargetedYahooRepairSpec:
    primary_dataset_publication_id: uuid.UUID
    entries: tuple[TargetedYahooRepairEntry, ...]
    created_by: str

    def __post_init__(self) -> None:
        if self.primary_dataset_publication_id != PRIMARY_V3_DATASET_PUBLICATION_ID:
            raise ValueError("Targeted Yahoo repairs must bind the exact primary v3 Dataset")
        if not self.created_by.strip() or self.created_by.strip() != self.created_by:
            raise ValueError("Targeted Yahoo repair creator must be normalized text")
        if not self.entries:
            raise ValueError("Targeted Yahoo repair requires at least one reviewed entry")
        keys = [item.gap_key for item in self.entries]
        if len(set(keys)) != len(keys):
            raise ValueError("Targeted Yahoo repair gap keys must be unique")


@dataclass(frozen=True, slots=True)
class TargetedYahooProviderIdentity:
    security_identifier_id: uuid.UUID
    security_id: uuid.UUID
    provider_symbol: str
    valid_from: date | None
    valid_to: date | None


@dataclass(frozen=True, slots=True)
class ValidatedYahooRepairPayload:
    sessions: tuple[date, ...]
    row_count: int
    payload_sha256: str


@dataclass(frozen=True, slots=True)
class TargetedYahooRepairPublication:
    security_id: uuid.UUID
    gap_key: str
    source_snapshot_artifact_id: uuid.UUID
    source_snapshot_security_subject_id: uuid.UUID
    alternate_observation_set_id: uuid.UUID
    alternate_observation_artifact_id: uuid.UUID
    review_artifact_id: uuid.UUID
    market_gap_resolution_id: uuid.UUID
    resolution_artifact_id: uuid.UUID
    expected_sessions: tuple[date, ...]
    reused: bool


class TargetedYahooRepairRepository:
    """Resolve exact published identities before any provider request is made."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def validate_primary_v3(self, dataset_publication_id: uuid.UUID) -> None:
        if dataset_publication_id != PRIMARY_V3_DATASET_PUBLICATION_ID:
            raise ValueError("Targeted Yahoo repairs must bind the exact primary v3 Dataset")
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    text(
                        """
                        SELECT publication.dataset_key,publication.version_number,
                               publication.value_kind,artifact.status
                          FROM data.dataset_publication publication
                          JOIN lineage.artifact artifact
                            ON artifact.artifact_id=publication.artifact_id
                         WHERE publication.dataset_publication_id=:dataset
                        """
                    ),
                    {"dataset": dataset_publication_id},
                )
                .mappings()
                .one_or_none()
            )
        if (
            row is None
            or row["status"] != "published"
            or row["dataset_key"] != PRIMARY_V3_DATASET_KEY
            or row["version_number"] != 3
            or row["value_kind"] != "daily_bar"
        ):
            raise LookupError("Exact published primary v3 daily-bar Dataset not found")

    def resolve_identity(self, entry: TargetedYahooRepairEntry) -> TargetedYahooProviderIdentity:
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    text(
                        """
                        SELECT identifier.security_identifier_id,identifier.security_id,
                               identifier.identifier_value AS provider_symbol,
                               identifier.valid_from,identifier.valid_to,
                               security.legacy_asset_id
                          FROM catalog.security_identifier identifier
                          JOIN catalog.security security
                            ON security.security_id=identifier.security_id
                         WHERE identifier.security_id=:security
                           AND identifier.provider_scope=:provider
                           AND identifier.identifier_type='provider_symbol'
                           AND identifier.identifier_value=:symbol
                           AND (identifier.valid_from IS NULL OR
                                identifier.valid_from<=:gap_start)
                           AND (identifier.valid_to IS NULL OR
                                :gap_end<identifier.valid_to)
                        """
                    ),
                    {
                        "security": entry.security_id,
                        "provider": _PROVIDER_SCOPE,
                        "symbol": entry.provider_symbol,
                        "gap_start": entry.gap_start,
                        "gap_end": entry.gap_end,
                    },
                )
                .mappings()
                .all()
            )
            has_primary_rows = connection.execute(
                text(
                    """
                    SELECT EXISTS(
                      SELECT 1 FROM data.daily_bar bar
                      JOIN catalog.security security
                        ON security.legacy_asset_id=bar.asset_id
                     WHERE bar.dataset_publication_id=:dataset
                       AND security.security_id=:security
                    )
                    """
                ),
                {
                    "dataset": PRIMARY_V3_DATASET_PUBLICATION_ID,
                    "security": entry.security_id,
                },
            ).scalar_one()
        if len(rows) != 1:
            raise ValueError(
                "Reviewed provider symbol does not resolve to one exact Security interval"
            )
        row = rows[0]
        if row["legacy_asset_id"] is None or not has_primary_rows:
            raise ValueError("Repair Security is not bridged into the primary v3 Dataset")
        return TargetedYahooProviderIdentity(
            cast(uuid.UUID, row["security_identifier_id"]),
            cast(uuid.UUID, row["security_id"]),
            str(row["provider_symbol"]),
            cast(date | None, row["valid_from"]),
            cast(date | None, row["valid_to"]),
        )

    def source_snapshot_id(self, artifact_id: uuid.UUID) -> uuid.UUID:
        with self._engine.connect() as connection:
            value = connection.execute(
                text(
                    "SELECT source_snapshot_id FROM data.source_snapshot "
                    "WHERE artifact_id=:artifact"
                ),
                {"artifact": artifact_id},
            ).scalar_one_or_none()
        if value is None:
            raise LookupError("Published Source Snapshot projection not found")
        return cast(uuid.UUID, value)


class TargetedYahooMarketRepairService:
    """Freeze and publish only explicitly reviewed, exact Yahoo replacements."""

    def __init__(
        self,
        engine: Engine,
        adapter: MarketSnapshotAdapter,
        *,
        repository: TargetedYahooRepairRepository | None = None,
        snapshots: _SnapshotPublisher | None = None,
        subjects: _SubjectPublisher | None = None,
        observations: _ObservationPublisher | None = None,
        artifacts: _ArtifactPublisher | None = None,
        resolutions: _ResolutionPublisher | None = None,
    ) -> None:
        self._adapter = adapter
        self._repository = repository or TargetedYahooRepairRepository(engine)
        self._snapshots = snapshots or SourceSnapshotService(engine)
        self._subjects = subjects or SourceSnapshotSecuritySubjectService(engine)
        self._observations = observations or AlternateObservationService(engine)
        self._artifacts = artifacts or ArtifactService(engine)
        self._resolutions = resolutions or MarketGapResolutionService(engine)

    def publish(self, spec: TargetedYahooRepairSpec) -> tuple[TargetedYahooRepairPublication, ...]:
        self._repository.validate_primary_v3(spec.primary_dataset_publication_id)

        # Resolve every identity and validate every provider response before the first write.
        # A malformed later entry therefore cannot leave a misleading partial repair batch.
        prepared: list[
            tuple[
                TargetedYahooRepairEntry,
                TargetedYahooProviderIdentity,
                RawFetch,
                ValidatedYahooRepairPayload,
            ]
        ] = []
        for entry in spec.entries:
            identity = self._repository.resolve_identity(entry)
            if (
                identity.security_id != entry.security_id
                or identity.provider_symbol != entry.provider_symbol
            ):
                raise ValueError("Resolved Yahoo provider identity does not match review entry")
            try:
                fetched = self._adapter.fetch(
                    entry.provider_symbol,
                    entry.gap_start,
                    entry.gap_end + timedelta(days=1),
                )
                validation = validate_targeted_yahoo_payload(entry, fetched)
            except (RuntimeError, ValueError) as error:
                raise ValueError(
                    f"Targeted Yahoo repair {entry.gap_key} failed validation: {error}"
                ) from error
            prepared.append((entry, identity, fetched, validation))

        return tuple(
            self._publish_entry(spec, entry, identity, fetched, validation)
            for entry, identity, fetched, validation in prepared
        )

    def _publish_entry(
        self,
        spec: TargetedYahooRepairSpec,
        entry: TargetedYahooRepairEntry,
        identity: TargetedYahooProviderIdentity,
        fetched: RawFetch,
        validation: ValidatedYahooRepairPayload,
    ) -> TargetedYahooRepairPublication:
        snapshot = self._snapshots.publish(
            fetched.snapshot_input(
                series_key=_SERIES_KEY,
                series_version=_SERIES_VERSION,
                snapshot_key=snapshot_key(
                    f"targeted-repair:{entry.security_id}:{entry.provider_symbol}",
                    fetched.fetched_at,
                ),
            )
        )
        source_snapshot_id = self._repository.source_snapshot_id(snapshot.artifact_id)
        subject = self._subjects.bind(
            source_snapshot_id=source_snapshot_id,
            security_id=entry.security_id,
            security_identifier_id=identity.security_identifier_id,
            fetch_status="fetched",
        )
        observation = self._observations.publish(
            AlternateObservationSetSpec(
                source_snapshot_security_subject_id=(subject.source_snapshot_security_subject_id),
                observation_key=f"targeted_yahoo_repair__{entry.gap_key}",
                version_number=entry.version_number,
                created_by=spec.created_by,
            )
        )
        review = self._publish_review(
            spec,
            entry=entry,
            identity=identity,
            validation=validation,
            snapshot_artifact_id=snapshot.artifact_id,
            observation=observation,
        )
        resolution = self._resolutions.publish(
            MarketGapResolutionSpec(
                primary_dataset_publication_id=spec.primary_dataset_publication_id,
                security_id=entry.security_id,
                gap_key=entry.gap_key,
                version_number=entry.version_number,
                gap_type=entry.gap_type,
                gap_start=entry.gap_start,
                gap_end=entry.gap_end,
                resolution_kind="replace_with_alternate",
                alternate_observation_set_id=observation.alternate_observation_set_id,
                evidence=(GapResolutionEvidenceRef(review.artifact_id, "provider_comparison"),),
                details={
                    "provider_scope": _PROVIDER_SCOPE,
                    "provider_symbol": entry.provider_symbol,
                    "reason": entry.reason,
                    "expected_sessions": [item.isoformat() for item in entry.expected_sessions],
                    "payload_sha256": validation.payload_sha256,
                    "validation_contract": _VALIDATION_CONTRACT,
                },
                created_by=spec.created_by,
            )
        )
        return TargetedYahooRepairPublication(
            security_id=entry.security_id,
            gap_key=entry.gap_key,
            source_snapshot_artifact_id=snapshot.artifact_id,
            source_snapshot_security_subject_id=(subject.source_snapshot_security_subject_id),
            alternate_observation_set_id=observation.alternate_observation_set_id,
            alternate_observation_artifact_id=observation.artifact_id,
            review_artifact_id=review.artifact_id,
            market_gap_resolution_id=resolution.market_gap_resolution_id,
            resolution_artifact_id=resolution.artifact_id,
            expected_sessions=entry.expected_sessions,
            reused=(snapshot.reused and observation.reused and review.reused and resolution.reused),
        )

    def _publish_review(
        self,
        spec: TargetedYahooRepairSpec,
        *,
        entry: TargetedYahooRepairEntry,
        identity: TargetedYahooProviderIdentity,
        validation: ValidatedYahooRepairPayload,
        snapshot_artifact_id: uuid.UUID,
        observation: AlternateObservationSetPublication,
    ) -> PublicationResult:
        document = {
            "contract_version": _REVIEW_CONTRACT,
            "validation_contract": _VALIDATION_CONTRACT,
            "primary_dataset_publication_id": str(spec.primary_dataset_publication_id),
            "reviewed_entry": _entry_document(entry),
            "resolved_identity": {
                "security_identifier_id": str(identity.security_identifier_id),
                "security_id": str(identity.security_id),
                "provider_scope": _PROVIDER_SCOPE,
                "provider_symbol": identity.provider_symbol,
                "valid_from": identity.valid_from.isoformat() if identity.valid_from else None,
                "valid_to": identity.valid_to.isoformat() if identity.valid_to else None,
            },
            "validation": {
                "sessions": [item.isoformat() for item in validation.sessions],
                "row_count": validation.row_count,
                "payload_sha256": validation.payload_sha256,
                "positive_volume_required": True,
                "positive_valid_ohlc_required": True,
                "exact_expected_sessions_required": True,
            },
            "source_snapshot_artifact_id": str(snapshot_artifact_id),
            "alternate_observation_artifact_id": str(observation.artifact_id),
            "reviewed_by": spec.created_by,
        }
        return self._artifacts.publish(
            artifact_type="v022_targeted_yahoo_market_repair_review",
            artifact_key=f"v022_targeted_yahoo_market_repair_review__{entry.gap_key}",
            version_number=entry.version_number,
            semantic_payload=document,
            content_payload=document,
            dependencies=(
                DependencyInput(snapshot_artifact_id, "source_snapshot", 0),
                DependencyInput(observation.artifact_id, "alternate_observation", 1),
            ),
            reason=f"publish reviewed targeted Yahoo replacement {entry.gap_key}",
        )


def validate_targeted_yahoo_payload(
    entry: TargetedYahooRepairEntry, fetched: RawFetch
) -> ValidatedYahooRepairPayload:
    """Validate the exact replacement before any immutable publication occurs."""

    requested_symbol = fetched.request_parameters.get("tickers")
    provider_symbol = fetched.request_parameters.get("provider_ticker")
    expected_provider_symbol = entry.provider_symbol.replace(".", "-")
    if requested_symbol != entry.provider_symbol or provider_symbol != expected_provider_symbol:
        raise ValueError("Fetched Yahoo payload identity does not match review entry")
    if not fetched.media_type.lower().startswith("text/csv"):
        raise ValueError("Targeted Yahoo repair requires a CSV Source Snapshot")

    reader = csv.DictReader(io.StringIO(fetched.payload.decode("utf-8-sig")))
    if reader.fieldnames is None:
        raise ValueError("Targeted Yahoo repair CSV header is absent")
    sessions: list[date] = []
    for row_number, row in enumerate(reader, start=2):
        try:
            session = date.fromisoformat(_csv_value(row, "session_date", "Date", "date"))
            if session in sessions:
                raise ValueError("duplicate session date")
            open_raw = _positive_decimal(row, "Open", "open")
            high_raw = _positive_decimal(row, "High", "high")
            low_raw = _positive_decimal(row, "Low", "low")
            close_raw = _positive_decimal(row, "Close", "close")
            if high_raw < max(open_raw, close_raw, low_raw):
                raise ValueError("high is below another OHLC value")
            if low_raw > min(open_raw, close_raw, high_raw):
                raise ValueError("low is above another OHLC value")
            volume = _decimal(row, "Volume", "volume")
            if volume != volume.to_integral_value() or volume <= 0:
                raise ValueError("volume must be a positive integer")
            adjusted = _optional_decimal(row, "Adj Close", "adj_close", "adjusted_close")
            if adjusted is not None and adjusted <= 0:
                raise ValueError("provider adjusted close must be positive")
            dividend = _optional_decimal(row, "Dividends", "dividends") or Decimal(0)
            split = _optional_decimal(row, "Stock Splits", "stock_splits") or Decimal(0)
            if dividend < 0 or split < 0:
                raise ValueError("corporate actions cannot be negative")
            sessions.append(session)
        except (InvalidOperation, KeyError, ValueError) as error:
            raise ValueError(f"Invalid targeted Yahoo repair row {row_number}: {error}") from error
    actual = tuple(sorted(sessions))
    if actual != entry.expected_sessions:
        missing = tuple(item for item in entry.expected_sessions if item not in actual)
        unexpected = tuple(item for item in actual if item not in entry.expected_sessions)
        raise ValueError(
            "Fetched Yahoo sessions do not exactly match the reviewed sessions; "
            f"missing={missing}, unexpected={unexpected}"
        )
    return ValidatedYahooRepairPayload(
        sessions=actual,
        row_count=len(actual),
        payload_sha256=hashlib.sha256(fetched.payload).hexdigest(),
    )


def _entry_document(entry: TargetedYahooRepairEntry) -> dict[str, object]:
    document = asdict(entry)
    document["security_id"] = str(entry.security_id)
    document["gap_start"] = entry.gap_start.isoformat()
    document["gap_end"] = entry.gap_end.isoformat()
    document["expected_sessions"] = [item.isoformat() for item in entry.expected_sessions]
    return document


def _csv_value(row: dict[str, str | None], *names: str) -> str:
    for name in names:
        value = row.get(name)
        if value is not None and value.strip():
            return value.strip()
    raise KeyError(names[0])


def _decimal(row: dict[str, str | None], *names: str) -> Decimal:
    value = Decimal(_csv_value(row, *names))
    if not value.is_finite():
        raise ValueError(f"{names[0]} must be finite")
    return value


def _positive_decimal(row: dict[str, str | None], *names: str) -> Decimal:
    value = _decimal(row, *names)
    if value <= 0:
        raise ValueError(f"{names[0]} must be positive")
    return value


def _optional_decimal(row: dict[str, str | None], *names: str) -> Decimal | None:
    for name in names:
        value = row.get(name)
        if value is not None and value.strip():
            result = Decimal(value.strip())
            if not result.is_finite():
                raise ValueError(f"{names[0]} must be finite")
            return result
    return None
