from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import cast

import pandas as pd
import pyarrow.parquet as parquet  # type: ignore[import-untyped]
from sqlalchemy import Engine, text

from style_rotation.data.providers.snapshots import RawFetch
from style_rotation.v022.dataset_gate import (
    DatasetGateAssessmentPublication,
    DatasetGateAssessmentService,
    DatasetGateAssessmentSpec,
    DatasetGateEvidenceRef,
    DatasetGateFinding,
    DatasetGateUniformExclusion,
)
from style_rotation.v022.frozen_sp500_seed import (
    DATASET_VERSION,
    FrozenSp500Preparation,
    FrozenSp500Seed,
)
from style_rotation.v022.security_market_data import (
    SecurityMarketDataPublicationService,
    SecurityMarketPublication,
    SecurityMarketPublicationSpec,
)
from style_rotation.v022.yahoo_ingestion import (
    YahooEquityContractPublication,
    YahooEquityContractService,
    YahooIngestionAttemptResult,
    YahooIngestionExecutionService,
    YahooIngestionPlanPublication,
    YahooIngestionPlanService,
    YahooIngestionPlanSpec,
    load_yahoo_equity_contract,
)

_DATASET_KEY = "us_sp500_historical_daily_free_research_v1"
_PLAN_KEY = "sp500_free_research_2004_2026_v2"
_COVERAGE_START = date(2004, 12, 31)


@dataclass(frozen=True, slots=True)
class FrozenSp500MarketPublication:
    contract: YahooEquityContractPublication
    plan: YahooIngestionPlanPublication
    fetched_segment_count: int
    unavailable_segment_count: int
    market: SecurityMarketPublication
    gate: DatasetGateAssessmentPublication


class FrozenParquetMarketSnapshotAdapter:
    """Expose the immutable upstream Parquet as exact Yahoo-shaped snapshots."""

    def __init__(self, seed: FrozenSp500Seed) -> None:
        self._seed = seed
        master = parquet.read_table(
            seed.paths.curated_root / "security_master.parquet",
            columns=["sid", "ticker"],
        ).to_pandas()
        self._sid_by_symbol = {
            str(row.ticker).casefold(): str(row.sid)
            for row in master.itertuples(index=False)
        }
        prices = parquet.read_table(
            seed.paths.curated_root / "prices_daily.parquet",
            columns=[
                "date",
                "sid",
                "raw_open",
                "raw_high",
                "raw_low",
                "raw_close",
                "tr_close",
                "volume",
                "dividends",
                "stock_splits",
            ],
        ).to_pandas()
        prices["date"] = pd.to_datetime(prices["date"])
        self._prices = prices.set_index(["sid", "date"]).sort_index()

    def fetch(self, symbol: str, start: date, end_exclusive: date) -> RawFetch:
        sid = self._sid_by_symbol.get(symbol.casefold())
        if sid is None:
            raise RuntimeError(f"Frozen dataset has no provider identity for {symbol}")
        try:
            frame = self._prices.xs(sid, level="sid")
        except KeyError as error:
            raise RuntimeError(f"Frozen dataset has no rows for {symbol}") from error
        start_at = pd.Timestamp(start)
        end_at = pd.Timestamp(end_exclusive)
        selected = frame.loc[(frame.index >= start_at) & (frame.index < end_at)].copy()
        selected = selected.dropna(
            subset=[
                "raw_open",
                "raw_high",
                "raw_low",
                "raw_close",
                "tr_close",
                "volume",
            ]
        )
        if selected.empty:
            raise RuntimeError(f"Frozen dataset has no usable market rows for {symbol}")
        output = pd.DataFrame(
            {
                "Open": selected["raw_open"],
                "High": selected["raw_high"],
                "Low": selected["raw_low"],
                "Close": selected["raw_close"],
                "Adj Close": selected["tr_close"],
                "Volume": selected["volume"],
                "Dividends": selected["dividends"].fillna(0),
                "Stock Splits": selected["stock_splits"].fillna(0),
            },
            index=selected.index,
        )
        output.index = output.index.strftime("%Y-%m-%d")
        output.index.name = "session_date"
        payload = output.to_csv(
            date_format="%Y-%m-%d",
            lineterminator="\n",
            na_rep="",
            float_format="%.15g",
        ).encode("utf-8")
        return RawFetch(
            requested_at=self._seed.frozen_at,
            fetched_at=self._seed.frozen_at,
            as_of_at=self._seed.frozen_at,
            media_type="text/csv; charset=utf-8",
            request_parameters={
                "tickers": symbol,
                "provider_ticker": symbol,
                "start": start.isoformat(),
                "end": end_exclusive.isoformat(),
                "interval": "1d",
                "source_mode": "frozen_parquet_replay",
            },
            response_metadata={
                "dataset_version": DATASET_VERSION,
                "manifest_sha256": self._seed.manifest_sha256,
                "historical_pit_claimed": False,
                "row_count": len(output),
            },
            payload=payload,
        )


class FrozenSp500MarketPublicationService:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def publish(
        self,
        seed: FrozenSp500Seed,
        preparation: FrozenSp500Preparation,
        *,
        contract_path: Path,
        created_by: str,
    ) -> FrozenSp500MarketPublication:
        contract = YahooEquityContractService(self._engine).publish(
            load_yahoo_equity_contract(contract_path)
        )
        plan = YahooIngestionPlanService(self._engine).publish(
            YahooIngestionPlanSpec(
                plan_key=_PLAN_KEY,
                version_number=2,
                universe_history_id=preparation.universe.universe_history_id,
                data_series_version_id=contract.data_series_version_id,
                coverage_start=_COVERAGE_START,
                coverage_end=seed.evaluation_end,
                created_by=created_by,
            )
        )
        attempts = self._execute(seed, plan.yahoo_ingestion_plan_id)
        failed = tuple(item for item in attempts if item.status == "failed")
        if failed:
            raise RuntimeError(
                f"Frozen S&P market replay failed for {len(failed)} segment(s): "
                f"{failed[0].failure_reason}"
            )
        calendar_artifact_id, cleaning_version_id = self._exact_runtime_inputs()
        market = SecurityMarketDataPublicationService(self._engine).publish(
            SecurityMarketPublicationSpec(
                yahoo_ingestion_plan_id=plan.yahoo_ingestion_plan_id,
                calendar_artifact_id=calendar_artifact_id,
                cleaning_version_id=cleaning_version_id,
                dataset_key=_DATASET_KEY,
                version_number=3,
                research_tier="rankable_research",
                created_by=created_by,
                market_gap_policy="free_source_warning",
            )
        )
        if market.dataset_publication_id is None:
            raise RuntimeError("Frozen S&P market quality gate did not publish a Dataset")
        gate = self._publish_gate(
            seed, preparation, plan, market, created_by=created_by
        )
        return FrozenSp500MarketPublication(
            contract,
            plan,
            sum(item.status == "fetched" for item in attempts),
            sum(item.status == "unavailable" for item in attempts),
            market,
            gate,
        )

    def _publish_gate(
        self,
        seed: FrozenSp500Seed,
        preparation: FrozenSp500Preparation,
        plan: YahooIngestionPlanPublication,
        market: SecurityMarketPublication,
        *,
        created_by: str,
    ) -> DatasetGateAssessmentPublication:
        if market.dataset_publication_id is None:
            raise RuntimeError("Dataset Gate requires a published frozen market Dataset")
        with self._engine.connect() as connection:
            ledger_id = connection.execute(
                text(
                    "SELECT universe_membership_ledger_id FROM "
                    "catalog.v022_universe_membership_ledger WHERE artifact_id=:artifact"
                ),
                {"artifact": preparation.universe.membership_ledger_artifact_id},
            ).scalar_one()
            report = connection.execute(
                text(
                    "SELECT report_document FROM data.v022_security_market_quality_report "
                    "WHERE security_market_quality_report_id=:report"
                ),
                {"report": market.quality_report_id},
            ).scalar_one()
            unavailable = connection.execute(
                text(
                    """
                    SELECT segment.security_id,attempt.failure_reason
                      FROM data.v022_yahoo_ingestion_segment segment
                      JOIN LATERAL (
                        SELECT item.* FROM data.v022_yahoo_ingestion_attempt item
                         WHERE item.yahoo_ingestion_segment_id=
                               segment.yahoo_ingestion_segment_id
                         ORDER BY item.attempt_ordinal DESC LIMIT 1
                      ) attempt ON true
                     WHERE segment.yahoo_ingestion_plan_id=:plan
                       AND attempt.attempt_status='unavailable'
                     ORDER BY segment.security_id
                    """
                ),
                {"plan": plan.yahoo_ingestion_plan_id},
            ).all()
        evidence = (
            DatasetGateEvidenceRef(
                preparation.universe_import_manifest.artifact_id, "supporting_evidence"
            ),
            DatasetGateEvidenceRef(
                preparation.import_manifest.artifact_id, "supporting_evidence"
            ),
        )
        issues = cast(dict[str, object], report).get("issues", [])
        gap_count = sum(
            isinstance(item, dict)
            and item.get("rule_code") == "active_member_market_bar_missing"
            for item in cast(list[object], issues)
        )
        findings: list[DatasetGateFinding] = [
            DatasetGateFinding(
                "historical_membership_retrospective",
                "membership",
                "warning",
                "none",
                "warning",
                evidence_artifact_id=preparation.universe_import_manifest.artifact_id,
                details={
                    "policy": "source_backed_retrospective_membership",
                    "historical_pit_claimed": False,
                },
            ),
            DatasetGateFinding(
                "retrospective_price_snapshot",
                "data_provenance",
                "warning",
                "none",
                "warning",
                evidence_artifact_id=preparation.import_manifest.artifact_id,
                details={
                    "provider": "yahoo_yfinance",
                    "price_semantics": "frozen_retrospective_yahoo_adjusted_price_snapshot",
                },
            ),
            DatasetGateFinding(
                "free_source_market_gaps",
                "market_coverage",
                "warning",
                "none",
                "warning",
                evidence_artifact_id=preparation.import_manifest.artifact_id,
                details={
                    "affected_security_count": gap_count,
                    "cohort_policy": "asset_date_eligibility_mask",
                },
            ),
        ]
        exclusions: list[DatasetGateUniformExclusion] = []
        for security_id, reason in unavailable:
            security = cast(uuid.UUID, security_id)
            findings.append(
                DatasetGateFinding(
                    "provider_uniformly_unavailable",
                    "uniform_exclusion",
                    "warning",
                    "none",
                    "warning",
                    security_id=security,
                    evidence_artifact_id=preparation.import_manifest.artifact_id,
                    details={"reason": str(reason)},
                )
            )
            exclusions.append(
                DatasetGateUniformExclusion(
                    security,
                    _COVERAGE_START,
                    seed.evaluation_end,
                    "frozen_free_source_provider_unavailable",
                    preparation.import_manifest.artifact_id,
                    {"reason": str(reason)},
                )
            )
        return DatasetGateAssessmentService(self._engine).publish(
            DatasetGateAssessmentSpec(
                dataset_publication_id=market.dataset_publication_id,
                universe_membership_ledger_id=cast(uuid.UUID, ledger_id),
                gate_key="sp500_free_research_v1",
                version_number=2,
                assessed_coverage_start=_COVERAGE_START,
                assessed_coverage_end=seed.evaluation_end,
                ranking_eligibility="rankable_research",
                product_eligibility="eligible_with_warnings",
                evidence=evidence,
                findings=tuple(findings),
                uniform_exclusions=tuple(exclusions),
                created_by=created_by,
            )
        )

    def _execute(
        self, seed: FrozenSp500Seed, plan_id: uuid.UUID
    ) -> tuple[YahooIngestionAttemptResult, ...]:
        status_by_key = {item.security_key: item.provider_status for item in seed.identities}
        with self._engine.connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT segment.yahoo_ingestion_segment_id,security.security_key,
                           segment.provider_symbol,segment.coverage_start,
                           segment.coverage_end
                      FROM data.v022_yahoo_ingestion_segment segment
                      JOIN catalog.security security
                        ON security.security_id=segment.security_id
                     WHERE segment.yahoo_ingestion_plan_id=:plan
                       AND NOT EXISTS (
                         SELECT 1 FROM data.v022_yahoo_ingestion_attempt attempt
                          WHERE attempt.yahoo_ingestion_segment_id=
                                segment.yahoo_ingestion_segment_id
                            AND attempt.attempt_status IN ('fetched','unavailable')
                       )
                     ORDER BY segment.ordinal
                    """
                ),
                {"plan": plan_id},
            ).all()
        if not rows:
            return ()
        adapter = FrozenParquetMarketSnapshotAdapter(seed)
        executor = YahooIngestionExecutionService(
            self._engine, adapter, clock=lambda: seed.frozen_at
        )
        results: list[YahooIngestionAttemptResult] = []
        for segment_id, security_key, provider_symbol, coverage_start, coverage_end in rows:
            unavailable_reason: str | None = None
            if status_by_key[str(security_key)] == "unavailable":
                unavailable_reason = "frozen_free_source_provider_unavailable"
            else:
                try:
                    adapter.fetch(
                        str(provider_symbol),
                        cast(date, coverage_start),
                        cast(date, coverage_end) + timedelta(days=1),
                    )
                except RuntimeError:
                    unavailable_reason = "frozen_free_source_effective_interval_unavailable"
            if unavailable_reason is not None:
                results.append(
                    executor.mark_unavailable(
                        cast(uuid.UUID, segment_id),
                        reason=unavailable_reason,
                    )
                )
            else:
                results.append(executor.execute_segment(cast(uuid.UUID, segment_id)))
        return tuple(results)

    def _exact_runtime_inputs(self) -> tuple[uuid.UUID, uuid.UUID]:
        with self._engine.connect() as connection:
            calendar = connection.execute(
                text(
                    """
                    SELECT version.artifact_id
                      FROM catalog.calendar_version version
                      JOIN catalog.calendar_definition definition
                        ON definition.calendar_definition_id=version.calendar_definition_id
                      JOIN lineage.artifact artifact ON artifact.artifact_id=version.artifact_id
                     WHERE definition.calendar_key='XNYS' AND version.version_number=5
                       AND artifact.status='published'
                    """
                )
            ).scalar_one()
            cleaning = connection.execute(
                text(
                    """
                    SELECT version.cleaning_version_id
                      FROM data.cleaning_version version
                      JOIN data.cleaning_definition definition
                        ON definition.cleaning_definition_id=version.cleaning_definition_id
                      JOIN lineage.artifact artifact ON artifact.artifact_id=version.artifact_id
                     WHERE definition.cleaning_key='adjusted_ohlc'
                       AND version.version_number=1 AND artifact.status='published'
                    """
                )
            ).scalar_one()
        return cast(uuid.UUID, calendar), cast(uuid.UUID, cleaning)
