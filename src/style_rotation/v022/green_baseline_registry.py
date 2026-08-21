from __future__ import annotations

import json
import uuid
from contextlib import nullcontext
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any, cast

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection, RowMapping

from style_rotation.catalog.versioning import semantic_version_number
from style_rotation.lineage.service import ArtifactService, DependencyInput

GREEN_BASELINE_REGISTRY_RELEASE_KEY = "v022_sp500_asset_registry"
GREEN_BASELINE_REGISTRY_CATALOG_VERSION = "0.22.4"
GREEN_BASELINE_RISK_DATASET_KEY = "us_sp500_free_research_frozen_v5_baseline"
GREEN_BASELINE_RISK_DATASET_VERSION = 1
GREEN_BASELINE_BENCHMARK_DATASET_KEY = "us_etf_daily_market_frozen_v6_baseline"
GREEN_BASELINE_BENCHMARK_DATASET_VERSION = 1
GREEN_BASELINE_GATE_KEY = "sp500_free_research_v1"
GREEN_BASELINE_GATE_VERSION = 5
GREEN_BASELINE_COHORT_VERSION = 11
GREEN_BASELINE_UNIVERSE_METHODOLOGY_KEY = "sp500_source_backed_green_membership_v1"


@dataclass(frozen=True, slots=True)
class GreenBaselineRegistryPublication:
    asset_registry_release_id: str
    artifact_id: str
    catalog_version: str
    profile_count: int
    stock_profile_count: int
    fund_profile_count: int
    candidate_profile_count: int
    excluded_profile_count: int
    reused: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class _BoundConnection:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def begin(self) -> nullcontext[Connection]:
        return nullcontext(self._connection)


class GreenBaselineRegistryService:
    """Publish the clean-green v0.22 Registry without any blue Registry lineage."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def publish(self, *, created_by: str) -> GreenBaselineRegistryPublication:
        if not created_by.strip():
            raise ValueError("green baseline Registry publisher is required")
        with self._engine.begin() as connection:
            inputs = _load_inputs(connection)
            profiles = _load_profiles(connection, inputs)
            document = _registry_document(inputs, profiles)
            release_id = uuid.uuid5(
                uuid.NAMESPACE_URL,
                "bird:v0.22:green-baseline-asset-registry:" + str(inputs["risk_id"]),
            )
            artifact = ArtifactService(cast(Engine, _BoundConnection(connection))).publish(
                artifact_type="asset_registry_release",
                artifact_key=GREEN_BASELINE_REGISTRY_RELEASE_KEY,
                version_number=semantic_version_number(
                    GREEN_BASELINE_REGISTRY_CATALOG_VERSION
                ),
                semantic_payload=document,
                content_payload=document,
                dependencies=(
                    DependencyInput(inputs["scope_artifact_id"], "security_identity", 0),
                    DependencyInput(inputs["universe_artifact_id"], "universe_history", 1),
                    DependencyInput(inputs["risk_artifact_id"], "canonical_market_dataset", 2),
                    DependencyInput(
                        inputs["benchmark_artifact_id"], "benchmark_market_dataset", 3
                    ),
                    DependencyInput(inputs["gate_artifact_id"], "dataset_gate_assessment", 4),
                    DependencyInput(inputs["weekly_runtime_artifact_id"], "weekly_runtime", 5),
                    DependencyInput(inputs["monthly_runtime_artifact_id"], "monthly_runtime", 6),
                ),
                reason="publish clean-green v0.22 executable Asset Registry 0.22.4",
                draft_writer=lambda bound, artifact_id: _write_registry(
                    bound,
                    artifact_id=artifact_id,
                    release_id=release_id,
                    as_of_date=inputs["coverage_end"],
                    profiles=profiles,
                ),
            )
        stock_count = sum(item["category_key"] == "stocks" for item in profiles)
        fund_count = sum(item["category_key"] == "funds" for item in profiles)
        excluded_count = sum(bool(item["uniformly_excluded"]) for item in profiles)
        return GreenBaselineRegistryPublication(
            str(release_id),
            str(artifact.artifact_id),
            GREEN_BASELINE_REGISTRY_CATALOG_VERSION,
            len(profiles),
            stock_count,
            fund_count,
            len(profiles) - excluded_count,
            excluded_count,
            artifact.reused,
        )


def _load_inputs(connection: Connection) -> RowMapping:
    row = connection.execute(
        text(
            """
            SELECT risk.dataset_publication_id AS risk_id,
                   risk.artifact_id AS risk_artifact_id,
                   risk.coverage_end,
                   benchmark.dataset_publication_id AS benchmark_id,
                   benchmark.artifact_id AS benchmark_artifact_id,
                   gate.dataset_gate_assessment_id AS gate_id,
                   gate.artifact_id AS gate_artifact_id,
                   history.universe_history_id,
                   history.artifact_id AS universe_artifact_id,
                   scope.artifact_id AS scope_artifact_id,
                   weekly.artifact_id AS weekly_runtime_artifact_id,
                   monthly.artifact_id AS monthly_runtime_artifact_id
              FROM data.dataset_publication risk
              JOIN lineage.artifact risk_artifact
                ON risk_artifact.artifact_id=risk.artifact_id
               AND risk_artifact.status='published'
              JOIN data.dataset_publication benchmark
                ON benchmark.dataset_key=:benchmark_key
               AND benchmark.version_number=:benchmark_version
               AND benchmark.calendar_version_id=risk.calendar_version_id
              JOIN lineage.artifact benchmark_artifact
                ON benchmark_artifact.artifact_id=benchmark.artifact_id
               AND benchmark_artifact.status='published'
              JOIN data.v022_dataset_gate_assessment gate
                ON gate.dataset_publication_id=risk.dataset_publication_id
               AND gate.gate_key=:gate_key AND gate.version_number=:gate_version
               AND gate.ranking_eligibility='rankable_research'
               AND gate.product_eligibility='eligible_with_warnings'
              JOIN lineage.artifact gate_artifact
                ON gate_artifact.artifact_id=gate.artifact_id
               AND gate_artifact.status='published'
              JOIN catalog.universe_history history
                ON history.universe_history_id=gate.universe_history_id
              JOIN catalog.universe_methodology methodology
                ON methodology.universe_methodology_id=history.universe_methodology_id
               AND methodology.methodology_key=:methodology_key
              JOIN lineage.artifact history_artifact
                ON history_artifact.artifact_id=history.artifact_id
               AND history_artifact.status='published'
              JOIN catalog.master_data_release scope
                ON scope.release_key='research_scope'
              JOIN lineage.artifact scope_artifact
                ON scope_artifact.artifact_id=scope.artifact_id
               AND scope_artifact.status='published'
              JOIN experiment.v022_evaluation_cohort_version weekly_cohort
                ON weekly_cohort.dataset_publication_id=risk.dataset_publication_id
               AND weekly_cohort.universe_history_id=history.universe_history_id
               AND weekly_cohort.frequency='weekly'
               AND weekly_cohort.version_number=:cohort_version
              JOIN experiment.v022_evaluation_cohort_runtime_contract weekly
                ON weekly.evaluation_cohort_version_id=weekly_cohort.evaluation_cohort_version_id
               AND weekly.dataset_gate_assessment_id=gate.dataset_gate_assessment_id
              JOIN lineage.artifact weekly_artifact
                ON weekly_artifact.artifact_id=weekly.artifact_id
               AND weekly_artifact.status='published'
              JOIN experiment.v022_evaluation_cohort_version monthly_cohort
                ON monthly_cohort.dataset_publication_id=risk.dataset_publication_id
               AND monthly_cohort.universe_history_id=history.universe_history_id
               AND monthly_cohort.frequency='monthly'
               AND monthly_cohort.version_number=:cohort_version
               AND monthly_cohort.benchmark_dataset_publication_id=
                   weekly_cohort.benchmark_dataset_publication_id
              JOIN experiment.v022_evaluation_cohort_runtime_contract monthly
                ON monthly.evaluation_cohort_version_id=monthly_cohort.evaluation_cohort_version_id
               AND monthly.dataset_gate_assessment_id=gate.dataset_gate_assessment_id
              JOIN lineage.artifact monthly_artifact
                ON monthly_artifact.artifact_id=monthly.artifact_id
               AND monthly_artifact.status='published'
             WHERE risk.dataset_key=:risk_key AND risk.version_number=:risk_version
            """
        ),
        {
            "risk_key": GREEN_BASELINE_RISK_DATASET_KEY,
            "risk_version": GREEN_BASELINE_RISK_DATASET_VERSION,
            "benchmark_key": GREEN_BASELINE_BENCHMARK_DATASET_KEY,
            "benchmark_version": GREEN_BASELINE_BENCHMARK_DATASET_VERSION,
            "gate_key": GREEN_BASELINE_GATE_KEY,
            "gate_version": GREEN_BASELINE_GATE_VERSION,
            "cohort_version": GREEN_BASELINE_COHORT_VERSION,
            "methodology_key": GREEN_BASELINE_UNIVERSE_METHODOLOGY_KEY,
        },
    ).mappings().one_or_none()
    if row is None:
        raise LookupError("clean-green Registry prerequisites are incomplete")
    return row


def _load_profiles(connection: Connection, inputs: RowMapping) -> list[dict[str, Any]]:
    rows = connection.execute(
        text(
            """
            WITH source AS (
              SELECT DISTINCT security.security_id,security.security_key,security.name,
                     security.currency,security.legacy_asset_id,
                     identifier.identifier_value AS symbol,'stocks'::text AS category_key,
                     'equity'::text AS asset_class,'Common Stock'::text AS instrument_type,
                     risk.dataset_publication_id AS dataset_id
                FROM data.dataset_coverage coverage
                JOIN data.dataset_publication risk
                  ON risk.dataset_publication_id=coverage.dataset_publication_id
                JOIN catalog.security security ON security.legacy_asset_id=coverage.asset_id
                JOIN catalog.security_identifier identifier
                  ON identifier.security_id=security.security_id
                 AND identifier.provider_scope='yahoo_yfinance'
                 AND identifier.identifier_type='provider_symbol'
               WHERE risk.dataset_publication_id=:risk
              UNION ALL
              SELECT DISTINCT security.security_id,security.security_key,security.name,
                     security.currency,security.legacy_asset_id,
                     identifier.identifier_value AS symbol,'funds'::text AS category_key,
                     'fund'::text AS asset_class,'ETF'::text AS instrument_type,
                     benchmark.dataset_publication_id AS dataset_id
                FROM data.dataset_coverage coverage
                JOIN data.dataset_publication benchmark
                  ON benchmark.dataset_publication_id=coverage.dataset_publication_id
                JOIN catalog.security security ON security.legacy_asset_id=coverage.asset_id
                JOIN catalog.security_identifier identifier
                  ON identifier.security_id=security.security_id
                 AND identifier.provider_scope='catalog'
                 AND identifier.identifier_type='symbol'
               WHERE benchmark.dataset_publication_id=:benchmark
            )
            SELECT source.*,(excluded.security_id IS NOT NULL) AS uniformly_excluded
              FROM source
              LEFT JOIN data.v022_dataset_gate_uniform_exclusion excluded
                ON excluded.dataset_gate_assessment_id=:gate
               AND excluded.security_id=source.security_id
             ORDER BY source.category_key DESC,lower(source.symbol),source.security_id
            """
        ),
        {"risk": inputs["risk_id"], "benchmark": inputs["benchmark_id"], "gate": inputs["gate_id"]},
    ).mappings().all()
    profiles = [dict(row) for row in rows]
    if len(profiles) != 626:
        raise ValueError("clean-green Registry requires exactly 621 stocks and 5 ETFs")
    symbols = [str(item["symbol"]).casefold() for item in profiles]
    if len(symbols) != len(set(symbols)):
        raise ValueError("clean-green Registry contains duplicate provider symbols")
    if sum(bool(item["uniformly_excluded"]) for item in profiles) != 31:
        raise ValueError(
            "clean-green Registry must project the 31 Gate exclusions that overlap v5"
        )
    return profiles


def _registry_document(inputs: RowMapping, profiles: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "contract_version": "v0.22.clean_green_asset_registry.v1",
        "catalog_version": GREEN_BASELINE_REGISTRY_CATALOG_VERSION,
        "as_of_date": inputs["coverage_end"].isoformat(),
        "risk_dataset_publication_id": str(inputs["risk_id"]),
        "benchmark_dataset_publication_id": str(inputs["benchmark_id"]),
        "dataset_gate_assessment_id": str(inputs["gate_id"]),
        "universe_history_id": str(inputs["universe_history_id"]),
        "profile_count": len(profiles),
        "candidate_profile_count": sum(not item["uniformly_excluded"] for item in profiles),
        "uniformly_excluded_security_ids": sorted(
            str(item["security_id"]) for item in profiles if item["uniformly_excluded"]
        ),
        "selection_policy": "exact_dataset_member_and_gate_uniform_exclusion_v1",
    }


def _write_registry(
    connection: Connection,
    *,
    artifact_id: uuid.UUID,
    release_id: uuid.UUID,
    as_of_date: date,
    profiles: list[dict[str, Any]],
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO catalog.asset_registry_release (
              asset_registry_release_id,artifact_id,release_key,version_number,
              catalog_version,as_of_date
            ) VALUES (:id,:artifact,:key,:version,:catalog_version,:as_of)
            """
        ),
        {
            "id": release_id,
            "artifact": artifact_id,
            "key": GREEN_BASELINE_REGISTRY_RELEASE_KEY,
            "version": semantic_version_number(GREEN_BASELINE_REGISTRY_CATALOG_VERSION),
            "catalog_version": GREEN_BASELINE_REGISTRY_CATALOG_VERSION,
            "as_of": as_of_date,
        },
    )
    categories = (
        ("stocks", "股票 / ADR", "冻结标普500历史研究股票；排除项保留身份但不可选择。"),
        ("funds", "ETF / ETP", "冻结基准与风格ETF日线资产。"),
    )
    category_ids: dict[str, uuid.UUID] = {}
    for ordinal, (key, name, description) in enumerate(categories):
        category_id = uuid.uuid5(release_id, "category:" + key)
        category_ids[key] = category_id
        connection.execute(
            text(
                """
                INSERT INTO catalog.asset_category (
                  asset_category_id,asset_registry_release_id,category_key,name,
                  description,ordinal
                ) VALUES (:id,:release,:key,:name,:description,:ordinal)
                """
            ),
            {
                "id": category_id,
                "release": release_id,
                "key": key,
                "name": name,
                "description": description,
                "ordinal": ordinal,
            },
        )
    for ordinal, item in enumerate(profiles):
        excluded = bool(item["uniformly_excluded"])
        tags = ["clean_green", "retrospective_price", "free_source"]
        if item["category_key"] == "stocks":
            tags.append("sp500_historical")
        if excluded:
            tags.append("uniformly_excluded")
        connection.execute(
            text(
                """
                INSERT INTO catalog.security_profile (
                  security_profile_id,asset_registry_release_id,security_id,
                  asset_category_id,symbol,aliases,asset_class,instrument_type,
                  tradability,venue_mic,calendar_key,tags,maturity,target_maturity,
                  missing_requirements,search_document,ordinal
                ) VALUES (
                  :id,:release,:security,:category,:symbol,CAST(:aliases AS jsonb),
                  :asset_class,:instrument_type,'tradable','XNYS','XNYS',
                  CAST(:tags AS jsonb),:maturity,:target_maturity,
                  CAST(:missing AS jsonb),:search_document,:ordinal
                )
                """
            ),
            {
                "id": uuid.uuid5(release_id, "profile:" + str(item["security_id"])),
                "release": release_id,
                "security": item["security_id"],
                "category": category_ids[str(item["category_key"])],
                "symbol": item["symbol"],
                "aliases": json.dumps([item["security_key"]]),
                "asset_class": item["asset_class"],
                "instrument_type": item["instrument_type"],
                "tags": json.dumps(tags, sort_keys=True),
                "maturity": "cataloged" if excluded else "research_ready",
                "target_maturity": "cataloged" if excluded else "product_eligible_input",
                "missing": json.dumps(
                    ["uniformly_excluded_by_gate"] if excluded else ["free_source_warning"]
                ),
                "search_document": " ".join(
                    str(value).casefold()
                    for value in (item["symbol"], item["security_key"], item["name"])
                ),
                "ordinal": ordinal,
            },
        )
    definition_id = uuid.uuid5(release_id, "asset-set:sp500_source_backed_green_membership_v1")
    connection.execute(
        text(
            """
            INSERT INTO catalog.asset_set_definition (
              asset_set_definition_id,asset_registry_release_id,set_key,name,
              set_type,maturity,formal_eligible,notes
            ) VALUES (
              :id,:release,:key,:name,'dynamic_methodology','research_ready',true,:notes
            )
            """
        ),
        {
            "id": definition_id,
            "release": release_id,
            "key": GREEN_BASELINE_UNIVERSE_METHODOLOGY_KEY,
            "name": "S&P 500 source-backed historical universe",
            "notes": "Dynamic PIT membership; daily eligibility is frozen by Cohort Runtime11.",
        },
    )
