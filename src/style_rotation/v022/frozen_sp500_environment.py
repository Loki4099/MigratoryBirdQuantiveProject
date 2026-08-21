from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import cast

from sqlalchemy import Engine, text
from sqlalchemy.engine import RowMapping

from style_rotation.v022.cohort_runtime_contract import (
    CohortRuntimeContractPublication,
    CohortRuntimeContractService,
)
from style_rotation.v022.evaluation_cohort import (
    EvaluationCohortPublication,
    EvaluationCohortPublicationService,
    EvaluationCohortSpec,
)

_RISK_DATASET_KEY = "us_sp500_free_research_frozen_v5_baseline"
_RISK_DATASET_VERSION = 1
_QUALITY_REPORT_KEY = "us_sp500_free_research_frozen_v5_baseline__quality"
_QUALITY_REPORT_VERSION = 1
_DATASET_GATE_KEY = "sp500_free_research_v1"
_DATASET_GATE_VERSION = 5
_UNIVERSE_METHODOLOGY_KEY = "sp500_source_backed_green_membership_v1"
_UNIVERSE_METHODOLOGY_VERSION = 1
_BENCHMARK_DATASET_KEY = "us_etf_daily_market_frozen_v6_baseline"
_BENCHMARK_DATASET_VERSION = 1
_WARMUP_START = date(2004, 12, 31)
_EVALUATION_START = date(2007, 1, 3)
_EVALUATION_END = date(2026, 6, 30)
FROZEN_SP500_COHORT_VERSION = 11


def frozen_sp500_cohort_key(frequency: str) -> str:
    if frequency not in {"weekly", "monthly"}:
        raise ValueError("Frozen S&P Cohort frequency must be weekly or monthly")
    return f"sp500_free_research_2007_2026_{frequency}_v{FROZEN_SP500_COHORT_VERSION}"


@dataclass(frozen=True, slots=True)
class FrozenSp500FrequencyEnvironment:
    frequency: str
    cohort: EvaluationCohortPublication
    runtime: CohortRuntimeContractPublication


@dataclass(frozen=True, slots=True)
class FrozenSp500CohortPublication:
    universe_history_id: uuid.UUID
    risk_dataset_publication_id: uuid.UUID
    benchmark_dataset_publication_id: uuid.UUID
    weekly: EvaluationCohortPublication
    monthly: EvaluationCohortPublication


@dataclass(frozen=True, slots=True)
class FrozenSp500EnvironmentPublication:
    universe_history_id: uuid.UUID
    risk_dataset_publication_id: uuid.UUID
    benchmark_dataset_publication_id: uuid.UUID
    dataset_gate_assessment_id: uuid.UUID
    weekly: FrozenSp500FrequencyEnvironment
    monthly: FrozenSp500FrequencyEnvironment


class FrozenSp500EnvironmentPublicationService:
    """Publish the two fixed-frequency environments from exact frozen data releases."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def publish(self, *, created_by: str) -> FrozenSp500EnvironmentPublication:
        """Idempotent convenience wrapper for the explicit two-phase publication."""

        self.publish_cohorts(created_by=created_by)
        return self.publish_runtimes(created_by=created_by)

    def publish_cohorts(self, *, created_by: str) -> FrozenSp500CohortPublication:
        """Publish Cohort v11 without requiring the not-yet-published Gate v5."""

        if not created_by.strip():
            raise ValueError("Frozen S&P environment publisher is required")
        frozen = self._load_exact_cohort_inputs()
        cohort_service = EvaluationCohortPublicationService(self._engine)
        publications: dict[str, EvaluationCohortPublication] = {}
        for frequency in ("weekly", "monthly"):
            cohort = cohort_service.publish(
                EvaluationCohortSpec(
                    cohort_key=frozen_sp500_cohort_key(frequency),
                    version_number=FROZEN_SP500_COHORT_VERSION,
                    research_tier="rankable_research",
                    frequency=frequency,
                    universe_history_id=cast(uuid.UUID, frozen["universe_history_id"]),
                    dataset_publication_id=cast(uuid.UUID, frozen["risk_dataset_publication_id"]),
                    benchmark_dataset_publication_id=cast(
                        uuid.UUID, frozen["benchmark_dataset_publication_id"]
                    ),
                    security_market_quality_report_id=cast(
                        uuid.UUID, frozen["security_market_quality_report_id"]
                    ),
                    calendar_version_id=cast(uuid.UUID, frozen["calendar_version_id"]),
                    warmup_start=_WARMUP_START,
                    evaluation_start=_EVALUATION_START,
                    evaluation_end=_EVALUATION_END,
                    cost_bps_per_side=Decimal("5"),
                    created_by=created_by,
                )
            )
            publications[frequency] = cohort
        return FrozenSp500CohortPublication(
            cast(uuid.UUID, frozen["universe_history_id"]),
            cast(uuid.UUID, frozen["risk_dataset_publication_id"]),
            cast(uuid.UUID, frozen["benchmark_dataset_publication_id"]),
            publications["weekly"],
            publications["monthly"],
        )

    def publish_runtimes(self, *, created_by: str) -> FrozenSp500EnvironmentPublication:
        """Bind exact published Cohort v11 identities to exact published Gate v5."""

        if not created_by.strip():
            raise ValueError("Frozen S&P environment publisher is required")
        frozen = self._load_exact_runtime_inputs()
        cohorts = self._load_exact_published_cohorts(frozen)
        runtime_service = CohortRuntimeContractService(self._engine)
        publications: dict[str, FrozenSp500FrequencyEnvironment] = {}
        for frequency in ("weekly", "monthly"):
            cohort = cohorts[frequency]
            runtime = runtime_service.publish(
                evaluation_cohort_version_id=cohort.evaluation_cohort_version_id,
                dataset_gate_assessment_id=cast(uuid.UUID, frozen["dataset_gate_assessment_id"]),
                created_by=created_by,
            )
            publications[frequency] = FrozenSp500FrequencyEnvironment(frequency, cohort, runtime)
        return FrozenSp500EnvironmentPublication(
            cast(uuid.UUID, frozen["universe_history_id"]),
            cast(uuid.UUID, frozen["risk_dataset_publication_id"]),
            cast(uuid.UUID, frozen["benchmark_dataset_publication_id"]),
            cast(uuid.UUID, frozen["dataset_gate_assessment_id"]),
            publications["weekly"],
            publications["monthly"],
        )

    def _load_exact_cohort_inputs(self) -> RowMapping:
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    text(
                        """
                    SELECT history.universe_history_id,
                           risk.dataset_publication_id AS risk_dataset_publication_id,
                           market.security_market_quality_report_id,
                           risk.calendar_version_id,
                           benchmark.dataset_publication_id AS
                             benchmark_dataset_publication_id,
                           history_artifact.status AS history_status,
                           risk_artifact.status AS risk_status,
                           benchmark_artifact.status AS benchmark_status,
                           report_artifact.status AS report_status
                      FROM data.dataset_publication risk
                      JOIN lineage.artifact risk_artifact
                        ON risk_artifact.artifact_id=risk.artifact_id
                      JOIN data.v022_reconciled_market_dataset_binding reconciled
                        ON reconciled.dataset_publication_id=
                           risk.dataset_publication_id
                       AND reconciled.dataset_artifact_id=risk.artifact_id
                      JOIN data.v022_security_market_dataset_binding market
                        ON market.dataset_publication_id=
                           reconciled.primary_dataset_publication_id
                      JOIN data.v022_security_market_quality_report report
                        ON report.security_market_quality_report_id=
                           market.security_market_quality_report_id
                       AND report.report_key=:report_key
                       AND report.version_number=:report_version
                      JOIN lineage.artifact report_artifact
                        ON report_artifact.artifact_id=report.artifact_id
                      JOIN catalog.universe_methodology methodology
                        ON methodology.methodology_key=:methodology_key
                       AND methodology.version_number=:methodology_version
                      JOIN catalog.universe_history history
                        ON history.universe_methodology_id=
                           methodology.universe_methodology_id
                      JOIN catalog.v022_universe_history_ledger_binding history_binding
                        ON history_binding.universe_history_id=
                           history.universe_history_id
                      JOIN catalog.v022_universe_membership_ledger ledger
                        ON ledger.universe_membership_ledger_id=
                           history_binding.universe_membership_ledger_id
                       AND ledger.research_tier='rankable_research'
                      JOIN lineage.artifact history_artifact
                        ON history_artifact.artifact_id=history.artifact_id
                      JOIN data.dataset_publication benchmark
                        ON benchmark.dataset_key=:benchmark_key
                       AND benchmark.version_number=:benchmark_version
                       AND benchmark.calendar_version_id=risk.calendar_version_id
                      JOIN lineage.artifact benchmark_artifact
                        ON benchmark_artifact.artifact_id=benchmark.artifact_id
                      JOIN catalog.asset spy ON spy.asset_key='spy'
                     WHERE risk.dataset_key=:risk_key
                       AND risk.version_number=:risk_version
                       AND EXISTS (
                         SELECT 1 FROM data.daily_bar bar
                          WHERE bar.dataset_publication_id=
                                benchmark.dataset_publication_id
                            AND bar.asset_id=spy.asset_id
                            AND bar.session_date BETWEEN :warmup AND :evaluation_end
                       )
                    """
                    ),
                    {
                        "risk_key": _RISK_DATASET_KEY,
                        "risk_version": _RISK_DATASET_VERSION,
                        "report_key": _QUALITY_REPORT_KEY,
                        "report_version": _QUALITY_REPORT_VERSION,
                        "methodology_key": _UNIVERSE_METHODOLOGY_KEY,
                        "methodology_version": _UNIVERSE_METHODOLOGY_VERSION,
                        "benchmark_key": _BENCHMARK_DATASET_KEY,
                        "benchmark_version": _BENCHMARK_DATASET_VERSION,
                        "warmup": _WARMUP_START,
                        "evaluation_end": _EVALUATION_END,
                    },
                )
                .mappings()
                .all()
            )
        if len(rows) != 1:
            raise ValueError(
                "Frozen S&P Cohort phase requires one exact risk, quality, "
                "universe, and SPY release"
            )
        row = rows[0]
        if any(
            row[key] != "published"
            for key in (
                "history_status",
                "risk_status",
                "benchmark_status",
                "report_status",
            )
        ):
            raise ValueError("Frozen S&P Cohort inputs must be published")
        return row

    def _load_exact_runtime_inputs(self) -> RowMapping:
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    text(
                        """
                    SELECT gate.universe_history_id,gate.dataset_publication_id AS
                             risk_dataset_publication_id,
                           gate.security_market_quality_report_id,
                           gate.dataset_gate_assessment_id,
                           risk.calendar_version_id,
                           benchmark.dataset_publication_id AS
                             benchmark_dataset_publication_id,
                           risk_artifact.status AS risk_status,
                           benchmark_artifact.status AS benchmark_status,
                           report_artifact.status AS report_status,
                           gate_artifact.status AS gate_status,
                           gate.ranking_eligibility,gate.product_eligibility
                      FROM data.v022_dataset_gate_assessment gate
                      JOIN data.dataset_publication risk
                        ON risk.dataset_publication_id=gate.dataset_publication_id
                       AND risk.dataset_key=:risk_key
                       AND risk.version_number=:risk_version
                      JOIN lineage.artifact risk_artifact
                        ON risk_artifact.artifact_id=risk.artifact_id
                      JOIN data.v022_security_market_quality_report report
                        ON report.security_market_quality_report_id=
                           gate.security_market_quality_report_id
                       AND report.report_key=:report_key
                       AND report.version_number=:report_version
                      JOIN lineage.artifact report_artifact
                        ON report_artifact.artifact_id=report.artifact_id
                      JOIN lineage.artifact gate_artifact
                        ON gate_artifact.artifact_id=gate.artifact_id
                      JOIN data.dataset_publication benchmark
                        ON benchmark.dataset_key=:benchmark_key
                       AND benchmark.version_number=:benchmark_version
                       AND benchmark.calendar_version_id=risk.calendar_version_id
                      JOIN lineage.artifact benchmark_artifact
                        ON benchmark_artifact.artifact_id=benchmark.artifact_id
                      JOIN catalog.asset spy ON spy.asset_key='spy'
                     WHERE gate.gate_key=:gate_key
                       AND gate.version_number=:gate_version
                       AND EXISTS (
                         SELECT 1 FROM data.daily_bar bar
                          WHERE bar.dataset_publication_id=
                                benchmark.dataset_publication_id
                            AND bar.asset_id=spy.asset_id
                            AND bar.session_date BETWEEN :warmup AND :evaluation_end
                       )
                    """
                    ),
                    {
                        "risk_key": _RISK_DATASET_KEY,
                        "risk_version": _RISK_DATASET_VERSION,
                        "report_key": _QUALITY_REPORT_KEY,
                        "report_version": _QUALITY_REPORT_VERSION,
                        "gate_key": _DATASET_GATE_KEY,
                        "gate_version": _DATASET_GATE_VERSION,
                        "benchmark_key": _BENCHMARK_DATASET_KEY,
                        "benchmark_version": _BENCHMARK_DATASET_VERSION,
                        "warmup": _WARMUP_START,
                        "evaluation_end": _EVALUATION_END,
                    },
                )
                .mappings()
                .all()
            )
        if len(rows) != 1:
            raise ValueError(
                "Frozen S&P environment requires one exact risk, Gate, and SPY release"
            )
        row = rows[0]
        if any(
            row[key] != "published"
            for key in ("risk_status", "benchmark_status", "report_status", "gate_status")
        ):
            raise ValueError("Frozen S&P environment inputs must be published")
        if row["ranking_eligibility"] != "rankable_research":
            raise ValueError("Frozen S&P Dataset Gate is not rankable")
        if row["product_eligibility"] not in {"eligible", "eligible_with_warnings"}:
            raise ValueError("Frozen S&P Dataset Gate cannot support a research Product")
        return row

    def _load_exact_published_cohorts(
        self, frozen: RowMapping
    ) -> dict[str, EvaluationCohortPublication]:
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    text(
                        """
                    SELECT cohort.frequency,cohort.evaluation_cohort_version_id,
                           cohort.artifact_id,cohort.cohort_fingerprint,
                           cohort.session_count,cohort.decision_session_count,
                           cohort.eligibility_interval_count,
                           artifact.status
                      FROM experiment.v022_evaluation_cohort_version cohort
                      JOIN lineage.artifact artifact
                        ON artifact.artifact_id=cohort.artifact_id
                     WHERE cohort.version_number=:version
                       AND cohort.cohort_key=(
                           'sp500_free_research_2007_2026_' || cohort.frequency ||
                           '_v' || CAST(:version AS text)
                       )
                       AND cohort.research_tier='rankable_research'
                       AND cohort.universe_history_id=:history
                       AND cohort.dataset_publication_id=:risk
                       AND cohort.benchmark_dataset_publication_id=:benchmark
                       AND cohort.security_market_quality_report_id=:report
                       AND cohort.calendar_version_id=:calendar
                       AND cohort.warmup_start=:warmup
                       AND cohort.evaluation_start=:evaluation_start
                       AND cohort.evaluation_end=:evaluation_end
                       AND cohort.cost_bps_per_side=5
                       AND cohort.execution_delay_sessions=1
                       AND cohort.benchmark_key='spy'
                    """
                    ),
                    {
                        "version": FROZEN_SP500_COHORT_VERSION,
                        "history": frozen["universe_history_id"],
                        "risk": frozen["risk_dataset_publication_id"],
                        "benchmark": frozen["benchmark_dataset_publication_id"],
                        "report": frozen["security_market_quality_report_id"],
                        "calendar": frozen["calendar_version_id"],
                        "warmup": _WARMUP_START,
                        "evaluation_start": _EVALUATION_START,
                        "evaluation_end": _EVALUATION_END,
                    },
                )
                .mappings()
                .all()
            )
        if len(rows) != 2 or {row["frequency"] for row in rows} != {
            "weekly",
            "monthly",
        }:
            raise ValueError(
                "Frozen S&P runtime phase requires exact published weekly and "
                "monthly Cohort v11 identities"
            )
        if any(row["status"] != "published" for row in rows):
            raise ValueError("Frozen S&P runtime Cohorts must be published")
        return {
            cast(str, row["frequency"]): EvaluationCohortPublication(
                cast(uuid.UUID, row["evaluation_cohort_version_id"]),
                cast(uuid.UUID, row["artifact_id"]),
                cast(str, row["cohort_fingerprint"]),
                cast(int, row["session_count"]),
                cast(int, row["decision_session_count"]),
                cast(int, row["eligibility_interval_count"]),
                True,
            )
            for row in rows
        }
