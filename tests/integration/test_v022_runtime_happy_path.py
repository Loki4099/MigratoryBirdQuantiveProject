from __future__ import annotations

import hashlib
import os
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Literal

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.engine import make_url

from style_rotation.api.app import _production_graph_suite_commands
from style_rotation.api.query import ArtifactQueryService
from style_rotation.catalog.asset_registry import (
    publish_asset_identities,
    publish_asset_registry,
)
from style_rotation.catalog.bootstrap import publish_catalogs
from style_rotation.catalog.scope import publish_research_scope
from style_rotation.data.bundle import (
    ReservePublicationService,
    publish_data_bundle,
    publish_reserve_model,
)
from style_rotation.data.calendar import CalendarPublicationService, XNYSCalendarGenerator
from style_rotation.data.forward_return_publication import publish_forward_return_catalog
from style_rotation.data.providers.snapshots import RawFetch
from style_rotation.data.publication import CanonicalDataPublicationService
from style_rotation.data.service import (
    SnapshotInput,
    SourceSnapshotService,
    publish_data_contracts,
)
from style_rotation.lineage.service import ArtifactService
from style_rotation.persistence.database import reset_database
from style_rotation.persistence.session import create_postgres_engine
from style_rotation.v022 import workspace_context
from style_rotation.v022.aggregation_work_runtime import (
    SignalManifestPoint,
    VerifiedAggregationInput,
)
from style_rotation.v022.cohort_runtime_contract import CohortRuntimeContractService
from style_rotation.v022.data_seed_import import (
    ExternalImportManifestService,
    ExternalImportManifestSpec,
    ExternalImportObjectSpec,
    ProviderSecurityIdentityService,
)
from style_rotation.v022.dataset_gate import (
    DatasetGateAssessmentService,
    DatasetGateAssessmentSpec,
    DatasetGateEvidenceRef,
    DatasetGateFinding,
)
from style_rotation.v022.draft_service import GraphDraftService
from style_rotation.v022.evaluation_cohort import (
    EvaluationCohortPublicationService,
    EvaluationCohortSpec,
)
from style_rotation.v022.frozen_sp500_environment import (
    FROZEN_SP500_COHORT_VERSION,
    frozen_sp500_cohort_key,
)
from style_rotation.v022.historical_universe import (
    HistoricalSp500UniversePublicationService,
    HistoricalSp500UniverseSpec,
    MembershipSecurityMapping,
    parse_fja_snapshot_csv,
)
from style_rotation.v022.payload_runtime import LocalPayloadObjectStore
from style_rotation.v022.product_promotion import ProductPromotionService
from style_rotation.v022.product_runtime_pipeline import ProductStrategyContract
from style_rotation.v022.product_runtime_worker import (
    _AggregationInputIdentity,
    _load_active_product_ensemble_state,
    _predict_product_ensemble,
    _RuntimeConfiguration,
)
from style_rotation.v022.publication import CatalogPublicationContext, publish_catalog_release
from style_rotation.v022.security_market_data import (
    SecurityMarketDataPublicationService,
    SecurityMarketPublicationSpec,
)
from style_rotation.v022.suite_launch_batch import (
    SuiteLaunchBatchRequest,
    SuiteLaunchBatchService,
)
from style_rotation.v022.suite_runtime_worker import SuiteRuntimeWorker
from style_rotation.v022.workspace_context import ActiveV022WorkspaceIdentity
from style_rotation.v022.workspace_view import GraphWorkspacePreviewService
from style_rotation.v022.yahoo_ingestion import (
    YahooEquityContractService,
    YahooIngestionExecutionService,
    YahooIngestionPlanService,
    YahooIngestionPlanSpec,
    load_yahoo_equity_contract,
)

pytestmark = pytest.mark.integration
DATABASE_URL = os.getenv("STYLE_ROTATION_TEST_DATABASE_URL")
PROJECT_ROOT = Path(__file__).parents[2]
CANDIDATE_MANIFEST = (
    PROJECT_ROOT / "v0.22" / "catalogs" / "releases" / "catalog_release.v0.22.13.json"
)
YAHOO_CONTRACT = (
    PROJECT_ROOT / "v0.22" / "catalogs" / "data_contracts" / "equity_market.v0.22.0.json"
)
CATALOG_CONTEXT = CatalogPublicationContext(
    actor_key="local_researcher",
    reviewer_actor="local_researcher",
    trusted_local_authorization_bootstrap=True,
)
ETF_ASSET_CONTEXT = "us_style_rotation_4_etf_sample_v1"
LARGE_CAP_ASSET_CONTEXT = "us_liquid_large_cap_300_pit_v1"
LARGE_CAP_SYMBOLS = (
    "AAPL",
    "MSFT",
    "NVDA",
    "AMZN",
    "GOOGL",
    "META",
    "LLY",
    "AVGO",
    "JPM",
    "V",
    "XOM",
    "UNH",
    "MA",
    "COST",
    "WMT",
    "HD",
    "PG",
    "JNJ",
    "ORCL",
    "ADBE",
    "AMD",
    "ABNB",
    "ALNY",
    "GOOG",
    "AEP",
    "AMGN",
    "ADI",
    "AMAT",
    "APP",
    "ADSK",
    "ADP",
    "AXON",
    "BKR",
    "BKNG",
    "CDNS",
    "CHTR",
    "CTAS",
    "CSCO",
    "CCEP",
    "CTSH",
    "CMCSA",
    "CEG",
    "CPRT",
    "CSGP",
    "CRWD",
    "CSX",
    "DDOG",
    "DXCM",
    "FANG",
    "DASH",
)
FIVE_DIMENSION_VOTE_FEATURES = (
    "return_continuation__w120",
    "lagged_return_continuation__l252_s20",
    "ma_trend_strength__s50_l200",
    "ppo_trend_acceleration__f12_s26_g9",
    "short_return_reversal__w5",
    "rsi_mean_reversion__w14",
    "deep_drawdown_reversal__w60",
    "low_skew_premium__w60",
    "low_kurtosis_quality__w120",
    "low_volatility__w60",
    "low_downside_risk__w60",
    "drawdown_resilience__w120",
    "dollar_volume_attention__w20",
    "low_illiquidity_quality__w20",
)


class _FrozenYahooAdapter:
    def __init__(self, sessions: tuple[date, ...]) -> None:
        self._sessions = sessions

    def fetch(self, symbol: str, start: date, end_exclusive: date) -> RawFetch:
        rows = ["session_date,Open,High,Low,Close,Adj Close,Volume,Dividends,Stock Splits"]
        symbol_offset = sum(ord(character) for character in symbol) % 47
        for index, session in enumerate(self._sessions):
            if not (start <= session < end_exclusive):
                continue
            close = 100 + symbol_offset + index * 0.04 + (index % 17) * 0.025
            rows.append(
                f"{session.isoformat()},{close - 0.15:.6f},{close + 0.40:.6f},"
                f"{close - 0.45:.6f},{close:.6f},{close:.6f},"
                f"{1_000_000 + symbol_offset * 10_000 + index * 100},0,0"
            )
        requested = datetime(2023, 1, 3, 1, tzinfo=UTC)
        fetched = datetime(2023, 1, 3, 1, 1, tzinfo=UTC)
        return RawFetch(
            requested_at=requested,
            fetched_at=fetched,
            as_of_at=fetched,
            media_type="text/csv; charset=utf-8",
            request_parameters={
                "tickers": symbol,
                "provider_ticker": symbol,
                "start": start.isoformat(),
                "end": end_exclusive.isoformat(),
                "interval": "1d",
                "auto_adjust": False,
                "actions": True,
            },
            response_metadata={"adapter": "frozen-test", "row_count": len(rows) - 1},
            payload=("\n".join(rows) + "\n").encode(),
        )


@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
@pytest.mark.parametrize(
    ("scenario_key", "asset_context_key", "selected_features", "configuration_events"),
    (
        (
            "representative",
            ETF_ASSET_CONTEXT,
            (
                "return_continuation__w120",
                "price_cross_above_ma__s1_l200",
                "low_illiquidity_quality__w20",
            ),
            (),
        ),
        (
            "catalog_parity",
            ETF_ASSET_CONTEXT,
            (
                "rsi_relative_strength__w14",
                "high_skew_regime__w60",
                "ppo_trend_acceleration__f12_s26_g9",
            ),
            (),
        ),
        (
            "single_signal_no_defense",
            ETF_ASSET_CONTEXT,
            ("rsi_relative_strength__w14",),
            (
                ("select_aggregation_family", {"family_key": "single_signal_identity"}),
                ("deselect_aggregation_family", {"family_key": "flat_equal_weight_mean"}),
            ),
        ),
        (
            "hierarchical_tail_distribution",
            ETF_ASSET_CONTEXT,
            ("low_skew_premium__w60", "low_kurtosis_quality__w120"),
            (
                ("select_aggregation_family", {"family_key": "hierarchical_weighted_mean"}),
                (
                    "set_aggregation_parameter_presets",
                    {
                        "family_key": "hierarchical_weighted_mean",
                        "preset_keys": ["legacy_dimension_equal_v1"],
                    },
                ),
                ("deselect_aggregation_family", {"family_key": "flat_equal_weight_mean"}),
            ),
        ),
        (
            "directional_equal_vote",
            ETF_ASSET_CONTEXT,
            FIVE_DIMENSION_VOTE_FEATURES,
            (
                ("select_aggregation_family", {"family_key": "directional_weighted_vote"}),
                (
                    "set_aggregation_parameter_presets",
                    {
                        "family_key": "directional_weighted_vote",
                        "preset_keys": ["legacy_equal_vote_v1"],
                    },
                ),
                ("deselect_aggregation_family", {"family_key": "flat_equal_weight_mean"}),
            ),
        ),
        (
            "directional_weighted_vote",
            ETF_ASSET_CONTEXT,
            FIVE_DIMENSION_VOTE_FEATURES,
            (
                ("select_aggregation_family", {"family_key": "directional_weighted_vote"}),
                (
                    "set_aggregation_parameter_presets",
                    {
                        "family_key": "directional_weighted_vote",
                        "preset_keys": ["legacy_weighted_vote_v1"],
                    },
                ),
                ("deselect_aggregation_family", {"family_key": "flat_equal_weight_mean"}),
            ),
        ),
        (
            "large_cap_k10",
            LARGE_CAP_ASSET_CONTEXT,
            ("rsi_relative_strength__w14",),
            (
                (
                    "set_strategy_parameter_presets",
                    {
                        "strategy_key": ("cross_section_rank_top_k_large_cap_multi_frequency"),
                        "preset_keys": ["k10"],
                    },
                ),
                (
                    "set_strategy_parameter_presets",
                    {
                        "strategy_key": "cross_section_rank_top_k_parity",
                        "preset_keys": [],
                    },
                ),
            ),
        ),
    ),
)
def test_graph_suite_reaches_typed_portfolio_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scenario_key: str,
    asset_context_key: str,
    selected_features: tuple[str, ...],
    configuration_events: tuple[tuple[str, dict[str, object]], ...],
) -> None:
    assert DATABASE_URL is not None
    database_name = make_url(DATABASE_URL).database
    assert database_name is not None
    reset_database(DATABASE_URL, database_name, "test")
    engine = create_postgres_engine(DATABASE_URL)
    try:
        risk_symbols = (
            LARGE_CAP_SYMBOLS if scenario_key == "large_cap_k10" else ("IWF", "IWD", "IWO", "IWN")
        )
        cohort_id, security_ids, _, _ = _publish_trainable_runtime_inputs(
            engine,
            frequency="weekly",
            risk_symbols=risk_symbols,
            risk_methodology_key=(
                LARGE_CAP_ASSET_CONTEXT
                if scenario_key == "large_cap_k10"
                else "sp500_historical_membership"
            ),
        )
        _patch_fixture_active_identity(engine, monkeypatch, cohort_id)
        publish_catalog_release(engine, CANDIDATE_MANIFEST, context=CATALOG_CONTEXT)
        drafts = GraphDraftService(
            engine, GraphWorkspacePreviewService.from_manifest(CANDIDATE_MANIFEST)
        )
        draft = drafts.create(
            researcher_key="runtime_happy_path",
            draft_key=f"runtime_happy_path_{scenario_key}",
            name=f"v0.22 runtime happy path: {scenario_key}",
            idempotency_key=uuid.uuid4(),
            asset_context_key=asset_context_key,
            data_input_keys=("canonical_market_bars",),
        )
        draft = drafts.apply_event(
            draft.graph_draft_id,
            expected_revision=draft.revision,
            actor_key="runtime_happy_path",
            idempotency_key=uuid.uuid4(),
            event_type="set_asset_selection",
            event={"security_ids": [str(item) for item in security_ids]},
        ).snapshot
        selected = drafts.apply_event(
            draft.graph_draft_id,
            expected_revision=draft.revision,
            actor_key="runtime_happy_path",
            idempotency_key=uuid.uuid4(),
            event_type="batch_select_feature_occurrences",
            event={
                "occurrences": [
                    {"feature_key": feature_key, "stage_no": 3} for feature_key in selected_features
                ]
            },
        ).snapshot
        base_configuration_events: tuple[tuple[str, dict[str, object]], ...] = (
            ("select_aggregation_family", {"family_key": "flat_equal_weight_mean"}),
            (
                "set_aggregation_parameter_presets",
                {
                    "family_key": "flat_equal_weight_mean",
                    "preset_keys": ["signal_equal_v1"],
                },
            ),
            (
                "set_strategy_parameter_presets",
                {
                    "strategy_key": "cross_section_rank_top_k_parity",
                    "preset_keys": ["k2"],
                },
            ),
            ("select_defense", {"defense_key": "none"}),
        )
        for event_type, event in base_configuration_events + configuration_events:
            selected = drafts.apply_event(
                draft.graph_draft_id,
                expected_revision=selected.revision,
                actor_key="runtime_happy_path",
                idempotency_key=uuid.uuid4(),
                event_type=event_type,
                event=event,
            ).snapshot
        compiled = drafts.compile(
            draft.graph_draft_id,
            expected_revision=selected.revision,
            actor_key="runtime_happy_path",
            idempotency_key=uuid.uuid4(),
        )

        monkeypatch.setattr(
            "style_rotation.v022.suite_runtime_planner.RUNTIME_CATALOG_VERSION",
            22014,
        )
        monkeypatch.setattr(
            "style_rotation.v022.suite_runtime_worker.RUNTIME_CATALOG_VERSION",
            22014,
        )

        commands = _production_graph_suite_commands(engine, payload_directory=tmp_path / "payloads")
        launched = SuiteLaunchBatchService(
            engine,
            graph_drafts=drafts,
            graph_suites=commands,
        ).submit(
            SuiteLaunchBatchRequest(
                actor_key="runtime_happy_path",
                idempotency_key=uuid.uuid4(),
                source_graph_draft_id=selected.graph_draft_id,
                source_graph_draft_revision=selected.revision,
                source_compiled_research_graph_id=compiled.compiled_research_graph_id,
                frequencies=("weekly",),
                suite_mode="exploratory",
            )
        )
        research_suite_id = launched["children"][0]["research_suite_id"]
        assert isinstance(research_suite_id, uuid.UUID)
        queued = commands.status(research_suite_id)
        assert queued["status"] == "not_started"
        assert queued["complete"] is False

        worker = SuiteRuntimeWorker(
            engine,
            payload_directory=tmp_path / "payloads",
            worker_key="runtime-happy-path-worker",
        )
        for _ in range(10):
            outcome = worker.run_once()
            if outcome.status == "completed":
                break
        else:
            raise AssertionError("v0.22 Suite worker did not complete the compiled DAG")
        status = commands.status(research_suite_id)

        assert status["status"] == "completed"
        assert status["terminal"] == status["total"]
        public_results = commands.results(research_suite_id)
        assert public_results["complete"] is True
        assert public_results["expected_result_count"] == 1
        assert public_results["result_count"] == 1
        assert public_results["results"][0]["quality_status"] == "passed"
        with engine.connect() as connection:
            result = (
                connection.execute(
                    text(
                        """
                    SELECT result.artifact_id AS result_artifact_id,
                           result.result_fingerprint,manifest.materialization_state,
                           result.quality_status
                      FROM experiment.v022_research_suite suite
                      JOIN experiment.v022_research_suite_graph_run_binding binding
                        ON binding.research_suite_id=suite.research_suite_id
                      JOIN workspace.v022_graph_work_consumer consumer
                        ON consumer.graph_run_id=binding.graph_run_id
                      JOIN workspace.v022_graph_work_item work
                        ON work.graph_work_item_id=consumer.graph_work_item_id
                       AND work.work_kind='portfolio_cell'
                      JOIN experiment.v022_portfolio_cell_runtime_result result
                        ON result.graph_work_item_id=consumer.graph_work_item_id
                      JOIN data.payload_manifest manifest
                        ON manifest.payload_manifest_id=result.payload_manifest_id
                     WHERE suite.research_suite_id=:suite
                    """
                    ),
                    {"suite": research_suite_id},
                )
                .mappings()
                .one()
            )
        assert len(result["result_fingerprint"]) == 64
        assert result["materialization_state"] == "materialized"
        assert result["quality_status"] == "passed"
        with engine.connect() as connection:
            evidence_id = connection.scalar(
                text(
                    "SELECT result_evidence_snapshot_id "
                    "FROM experiment.v022_result_evidence_snapshot "
                    "WHERE result_artifact_id=:result"
                ),
                {"result": result["result_artifact_id"]},
            )
        assert isinstance(evidence_id, uuid.UUID)
        if scenario_key == "representative":
            today = datetime.now(UTC).date()
            CalendarPublicationService(engine).publish(
                XNYSCalendarGenerator().generate(
                    today + timedelta(days=1), today + timedelta(days=120)
                ),
                version_number=2,
            )
            promotion_key = uuid.uuid4()
            promotions = ProductPromotionService(engine)
            promoted = promotions.promote_and_enroll(
                result_evidence_snapshot_id=evidence_id,
                actor_key="runtime_happy_path",
                idempotency_key=promotion_key,
                product_key="v022_runtime_happy_path_candidate",
                name="v0.22 runtime happy path candidate",
                description="Deterministic Product integration fixture",
                version_number=1,
            )
            replay = promotions.promote_and_enroll(
                result_evidence_snapshot_id=evidence_id,
                actor_key="runtime_happy_path",
                idempotency_key=promotion_key,
                product_key="v022_runtime_happy_path_candidate",
                name="v0.22 runtime happy path candidate",
                description="Deterministic Product integration fixture",
                version_number=1,
            )
            assert promoted["lifecycle"] == "active"
            assert promoted["reused"] is False
            assert replay["reused"] is True
            assert promoted["product_data_disclosure_id"] is not None
            assert promoted["product_ensemble_state_id"] is None
            detail = ArtifactQueryService(engine).v022_product_identity_detail(
                uuid.UUID(promoted["product_enrollment_id"])
            )
            assert detail["data_disclosure"]["product_eligibility"] in {
                "eligible",
                "eligible_with_warnings",
            }
    finally:
        engine.dispose()


@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
@pytest.mark.parametrize(
    ("frequency", "family_key", "target_keys", "training_preset_keys"),
    (
        (
            "weekly",
            "ols_cross_sectional_regression",
            ("forward_rank_h5",),
            ("expanding_daily_ols_v1",),
        ),
        (
            "monthly",
            "ols_cross_sectional_regression",
            ("forward_rank_h5",),
            ("expanding_daily_ols_v1",),
        ),
        (
            "weekly",
            "ridge_cross_sectional_regression",
            ("forward_rank_h5",),
            ("expanding_daily_ridge_alpha1_v1",),
        ),
        (
            "monthly",
            "ridge_cross_sectional_regression",
            ("forward_rank_h5",),
            ("expanding_daily_ridge_alpha1_v1",),
        ),
        (
            "weekly",
            "random_forest_cross_sectional_regression",
            ("forward_rank_h5",),
            ("expanding_daily_rf_balanced_v1",),
        ),
        (
            "monthly",
            "random_forest_cross_sectional_regression",
            ("forward_rank_h5",),
            ("expanding_daily_rf_balanced_v1",),
        ),
        (
            "weekly",
            "lightgbm_cross_sectional_regression",
            ("forward_rank_h5",),
            ("expanding_daily_lightgbm_balanced_v1",),
        ),
        (
            "monthly",
            "lightgbm_cross_sectional_regression",
            ("forward_rank_h5",),
            ("expanding_daily_lightgbm_balanced_v1",),
        ),
        (
            "weekly",
            "xgboost_cross_sectional_regression",
            ("forward_rank_h5",),
            ("expanding_daily_xgb_balanced_v1",),
        ),
        (
            "monthly",
            "xgboost_cross_sectional_regression",
            ("forward_rank_h5",),
            ("expanding_daily_xgb_balanced_v1",),
        ),
        (
            "weekly",
            "xgboost_cross_sectional_regression",
            ("forward_rank_h5", "forward_rank_h10"),
            (
                "expanding_daily_xgb_balanced_v1",
                "expanding_daily_xgb_feature_subsample_v1",
            ),
        ),
    ),
)
def test_candidate_catalog_runs_one_exact_supervised_aggregation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    frequency: Literal["weekly", "monthly"],
    family_key: str,
    target_keys: tuple[str, ...],
    training_preset_keys: tuple[str, ...],
) -> None:
    assert DATABASE_URL is not None
    database_name = make_url(DATABASE_URL).database
    assert database_name is not None
    reset_database(DATABASE_URL, database_name, "test")
    engine = create_postgres_engine(DATABASE_URL)
    try:
        cohort_id, security_ids, evaluation_start, evaluation_end = (
            _publish_trainable_runtime_inputs(engine, frequency=frequency)
        )
        _patch_fixture_active_identity(engine, monkeypatch, cohort_id)
        publish_catalog_release(engine, CANDIDATE_MANIFEST, context=CATALOG_CONTEXT)
        drafts = GraphDraftService(
            engine, GraphWorkspacePreviewService.from_manifest(CANDIDATE_MANIFEST)
        )
        selected = drafts.create(
            researcher_key="runtime_supervised_candidate",
            draft_key="runtime_supervised_candidate",
            name="v0.22 supervised candidate compile",
            idempotency_key=uuid.uuid4(),
            asset_context_key=ETF_ASSET_CONTEXT,
            data_input_keys=("canonical_market_bars",),
            frequency=frequency,
        )
        events: tuple[tuple[str, dict[str, object]], ...] = (
            (
                "set_asset_selection",
                {"security_ids": [str(item) for item in security_ids]},
            ),
            (
                "batch_select_feature_occurrences",
                {"occurrences": [{"feature_key": "return_continuation__w120", "stage_no": 3}]},
            ),
            ("select_aggregation_family", {"family_key": family_key}),
            ("deselect_aggregation_family", {"family_key": "flat_equal_weight_mean"}),
            (
                "set_aggregation_targets",
                {
                    "family_key": family_key,
                    "target_keys": list(target_keys),
                },
            ),
            (
                "set_aggregation_training_presets",
                {
                    "family_key": family_key,
                    "preset_keys": list(training_preset_keys),
                },
            ),
            (
                "set_strategy_parameter_presets",
                {
                    "strategy_key": "cross_section_rank_top_k_parity",
                    "preset_keys": ["k2"],
                },
            ),
            ("select_defense", {"defense_key": "none"}),
        )
        for event_type, event in events:
            selected = drafts.apply_event(
                selected.graph_draft_id,
                expected_revision=selected.revision,
                actor_key="runtime_supervised_candidate",
                idempotency_key=uuid.uuid4(),
                event_type=event_type,
                event=event,
            ).snapshot
        assert selected.derived_view["blockers"] == []
        compiled = drafts.compile(
            selected.graph_draft_id,
            expected_revision=selected.revision,
            actor_key="runtime_supervised_candidate",
            idempotency_key=uuid.uuid4(),
        )
        with engine.connect() as connection:
            row = (
                connection.execute(
                    text(
                        """
                    SELECT family.family_key,target.target_key,
                           training.training_preset_key,
                           ensemble.member_count,ensemble.target_group_count,
                           schema.ordered_feature_document
                      FROM workspace.compiled_aggregation_instance instance
                      JOIN aggregation.aggregation_version version
                        ON version.aggregation_version_id=instance.aggregation_version_id
                      JOIN aggregation.aggregation_family family
                        ON family.aggregation_family_id=version.aggregation_family_id
                      LEFT JOIN aggregation.target_version target_version
                        ON target_version.target_version_id=instance.target_version_id
                      LEFT JOIN aggregation.target_definition target
                        ON target.target_definition_id=target_version.target_definition_id
                      LEFT JOIN aggregation.training_preset_version training_version
                        ON training_version.training_preset_version_id=
                           instance.training_preset_version_id
                      LEFT JOIN aggregation.training_preset_definition training
                        ON training.training_preset_definition_id=
                           training_version.training_preset_definition_id
                      LEFT JOIN workspace.v022_compiled_trainable_ensemble_binding
                        ensemble_binding
                        ON ensemble_binding.compiled_aggregation_instance_id=
                           instance.compiled_aggregation_instance_id
                      LEFT JOIN aggregation.v022_trainable_ensemble_spec ensemble
                        ON ensemble.ensemble_spec_id=ensemble_binding.ensemble_spec_id
                      JOIN workspace.v022_compiled_feature_schema_binding
                        schema_binding
                        ON schema_binding.compiled_aggregation_instance_id=
                           instance.compiled_aggregation_instance_id
                      JOIN aggregation.v022_feature_schema_version schema
                        ON schema.feature_schema_version_id=
                           schema_binding.feature_schema_version_id
                     WHERE instance.compiled_research_graph_id=:graph
                    """
                    ),
                    {"graph": compiled.compiled_research_graph_id},
                )
                .mappings()
                .one()
            )
        assert row["family_key"] == family_key
        expected_member_count = len(target_keys) * len(training_preset_keys)
        if expected_member_count == 1:
            assert row["target_key"] == f"{family_key}__{target_keys[0]}"
            assert row["training_preset_key"] == (f"{family_key}__{training_preset_keys[0]}")
            assert row["member_count"] is None
            assert row["target_group_count"] is None
        else:
            assert row["target_key"] is None
            assert row["training_preset_key"] is None
            assert row["member_count"] == expected_member_count
            assert row["target_group_count"] == len(target_keys)
        assert row["ordered_feature_document"]["ordered_feature_keys"] == [
            "return_continuation__w120"
        ]

        candidate_version = 22014
        monkeypatch.setattr(
            "style_rotation.v022.suite_runtime_planner.RUNTIME_CATALOG_VERSION",
            candidate_version,
        )
        monkeypatch.setattr(
            "style_rotation.v022.suite_runtime_worker.RUNTIME_CATALOG_VERSION",
            candidate_version,
        )
        commands = _production_graph_suite_commands(
            engine, payload_directory=tmp_path / "supervised-payloads"
        )
        launched = SuiteLaunchBatchService(
            engine,
            graph_drafts=drafts,
            graph_suites=commands,
        ).submit(
            SuiteLaunchBatchRequest(
                actor_key="runtime_supervised_candidate",
                idempotency_key=uuid.uuid4(),
                source_graph_draft_id=selected.graph_draft_id,
                source_graph_draft_revision=selected.revision,
                source_compiled_research_graph_id=compiled.compiled_research_graph_id,
                frequencies=(frequency,),
                suite_mode="exploratory",
            )
        )
        suite_id = launched["children"][0]["research_suite_id"]
        assert isinstance(suite_id, uuid.UUID)
        worker = SuiteRuntimeWorker(
            engine,
            payload_directory=tmp_path / "supervised-payloads",
            worker_key="runtime-supervised-candidate-worker",
        )
        for _ in range(10):
            outcome = worker.run_once()
            if outcome.status == "completed":
                break
            if outcome.status == "failed":
                raise AssertionError("Supervised Suite worker failed its compiled DAG")
        else:
            raise AssertionError("Supervised Suite worker did not complete its compiled DAG")
        status = commands.status(suite_id)
        assert status["status"] == "completed"
        public = commands.results(suite_id)
        assert public["result_count"] == 1
        assert public["results"][0]["quality_status"] == "passed"
        assert public["results"][0]["effective_start"] == evaluation_start
        assert public["results"][0]["effective_end"] == evaluation_end
        with engine.connect() as connection:
            publication_counts = (
                connection.execute(
                    text(
                        """
                    SELECT
                          (SELECT count(*) FROM aggregation.v022_training_matrix) AS matrices,
                          (SELECT count(*) FROM aggregation.v022_training_fold) AS folds,
                          (SELECT count(*) FROM aggregation.v022_base_learner_spec) AS specs,
                          (SELECT count(*) FROM aggregation.v022_fitted_model_state) AS states,
                          (SELECT count(*) FROM aggregation.v022_oof_prediction) AS predictions,
                          (SELECT count(*) FROM aggregation.v022_trainable_aggregation_diagnostic)
                            AS diagnostics,
                      (SELECT count(*) FROM experiment.v022_research_suite_evaluation_cohort_binding
                        WHERE research_suite_id=:suite
                          AND evaluation_cohort_version_id=:cohort) AS exact_cohort
                    """
                    ),
                    {"suite": suite_id, "cohort": cohort_id},
                )
                .mappings()
                .one()
            )
        assert publication_counts["matrices"] == len(target_keys)
        assert publication_counts["folds"] >= 1
        assert publication_counts["specs"] == expected_member_count
        assert publication_counts["states"] == (
            publication_counts["folds"] * len(training_preset_keys)
        )
        assert publication_counts["predictions"] == expected_member_count
        assert publication_counts["diagnostics"] == 1
        assert publication_counts["exact_cohort"] == 1
        if frequency == "weekly" and (
            (
                family_key == "ols_cross_sectional_regression"
                and target_keys == ("forward_rank_h5",)
                and training_preset_keys == ("expanding_daily_ols_v1",)
            )
            or (
                family_key == "xgboost_cross_sectional_regression"
                and target_keys == ("forward_rank_h5", "forward_rank_h10")
                and training_preset_keys
                == (
                    "expanding_daily_xgb_balanced_v1",
                    "expanding_daily_xgb_feature_subsample_v1",
                )
            )
        ):
            today = datetime.now(UTC).date()
            CalendarPublicationService(engine).publish(
                XNYSCalendarGenerator().generate(
                    today + timedelta(days=1), today + timedelta(days=120)
                ),
                version_number=2,
            )
            with engine.connect() as connection:
                evidence_id = connection.scalar(
                    text(
                        "SELECT result_evidence_snapshot_id FROM "
                        "experiment.v022_result_evidence_snapshot "
                        "ORDER BY created_at DESC LIMIT 1"
                    )
                )
            assert isinstance(evidence_id, uuid.UUID)
            promoted = ProductPromotionService(engine).promote_and_enroll(
                result_evidence_snapshot_id=evidence_id,
                actor_key="runtime_supervised_product",
                idempotency_key=uuid.uuid4(),
                product_key=f"runtime_supervised_product_{family_key}",
                name="Runtime supervised Product",
                description="Supervised Product state integration fixture",
                version_number=1,
            )
            assert promoted["product_ensemble_state_id"] is not None
            assert promoted["product_ensemble_state_artifact_id"] is not None
            assert len(promoted["product_ensemble_state_fingerprint"]) == 64
            payload_root = tmp_path / "supervised-payloads"
            state = _load_active_product_ensemble_state(
                engine,
                LocalPayloadObjectStore(payload_root),
                product_enrollment_id=uuid.UUID(promoted["product_enrollment_id"]),
                decision_session_id=uuid.UUID(promoted["first_eligible_decision_session_id"]),
                execution_mode="supervised",
            )
            assert state is not None
            assert len(state.members) == expected_member_count
            product_detail = ArtifactQueryService(engine).v022_product_identity_detail(
                uuid.UUID(promoted["product_enrollment_id"])
            )
            assert product_detail["active_ensemble_state"] is not None
            assert (
                product_detail["active_ensemble_state"]["state_fingerprint"]
                == promoted["product_ensemble_state_fingerprint"]
            )
            assert product_detail["active_ensemble_state"]["member_count"] == expected_member_count
            with engine.connect() as connection:
                session = connection.execute(
                    text(
                        "SELECT session_date,decision_cutoff_at FROM "
                        "product.v022_decision_schedule_session WHERE "
                        "decision_session_id=:session"
                    ),
                    {"session": uuid.UUID(promoted["first_eligible_decision_session_id"])},
                ).one()
                asset_rows = tuple(
                    connection.execute(
                        text(
                            "SELECT security_id,security_key FROM catalog.security "
                            "WHERE security_id=ANY(:ids) ORDER BY security_key"
                        ),
                        {"ids": list(security_ids)},
                    )
                )
            feature_input = VerifiedAggregationInput(
                uuid.uuid4(),
                "return_continuation__w120",
                "signal_000",
                0,
                uuid.uuid4(),
                uuid.uuid4(),
                "a" * 64,
                tuple(
                    SignalManifestPoint(
                        row.security_id,
                        row.security_key,
                        session.session_date,
                        Decimal(ordinal),
                        session.decision_cutoff_at,
                        "b" * 64,
                        None,
                    )
                    for ordinal, row in enumerate(asset_rows)
                ),
            )
            configuration = _RuntimeConfiguration(
                uuid.uuid4(),
                uuid.uuid4(),
                family_key,
                None,
                (
                    _AggregationInputIdentity(
                        feature_input.compiled_feature_occurrence_id,
                        feature_input.feature_variant_key,
                        feature_input.slot_key,
                        0,
                    ),
                ),
                None,
                ProductStrategyContract(
                    "cross_section_rank_top_k_parity", 2, "formal", "none", "none"
                ),
                None,
                None,
                None,
                None,
                "supervised",
            )
            calculation = _predict_product_ensemble(
                configuration,
                (feature_input,),
                state,
                decision_date=session.session_date,
                decision_cutoff_at=session.decision_cutoff_at,
            )
            assert len(calculation.points) == len(security_ids)
            assert all(
                point.input_revision == state.state_fingerprint for point in calculation.points
            )
    finally:
        engine.dispose()


def _publish_trainable_runtime_inputs(
    engine: Engine,
    *,
    frequency: Literal["weekly", "monthly"],
    risk_symbols: tuple[str, ...] = ("IWF", "IWD", "IWO", "IWN"),
    risk_methodology_key: str = "sp500_historical_membership",
) -> tuple[uuid.UUID, tuple[uuid.UUID, ...], date, date]:
    publish_research_scope(engine, PROJECT_ROOT / "v0.2/catalogs/research_scope.v0.2.0.json")
    asset_catalog = PROJECT_ROOT / "v0.21/catalogs/assets.v0.21.1.json"
    publish_asset_registry(engine, asset_catalog)
    publish_asset_identities(engine, asset_catalog)
    publish_data_contracts(engine, PROJECT_ROOT / "v0.2/catalogs/data_contracts.v0.2.0.json")
    publish_catalogs(ArtifactService(engine), PROJECT_ROOT / "v0.2/catalogs")
    publish_forward_return_catalog(
        engine, PROJECT_ROOT / "v0.2/catalogs/forward_returns.v0.2.0.json"
    )
    generated_full = XNYSCalendarGenerator().generate(date(2020, 1, 2), date(2022, 6, 30))
    sessions = tuple(item.session_date for item in generated_full.sessions)
    if len(sessions) < 560:
        raise AssertionError("Trainable runtime fixture requires at least 560 sessions")
    warmup_start = sessions[0]
    evaluation_start = sessions[504]
    evaluation_end = sessions[545]
    post_end = sessions[558]
    generated = XNYSCalendarGenerator().generate(warmup_start, post_end)
    calendar = CalendarPublicationService(engine).publish(generated)
    # Keep the four selectable ETFs and the SPY benchmark in independent
    # Datasets, matching the production Cohort contract.  A benchmark Security
    # must never become a selectable strategy member merely because the fixture
    # uses a compact source adapter.
    benchmark_symbols = ("SPY",)
    symbols = risk_symbols + benchmark_symbols
    with engine.connect() as connection:
        securities = tuple(
            connection.execute(
                text(
                    "SELECT security_key,security_id FROM catalog.security "
                    "WHERE security_key=ANY(:keys) ORDER BY array_position(:keys,security_key)"
                ),
                {"keys": [item.lower() for item in symbols]},
            ).mappings()
        )
        cleaning_version_id = connection.scalar(
            text(
                """
                SELECT version.cleaning_version_id
                  FROM data.cleaning_version version
                  JOIN data.cleaning_definition definition
                    ON definition.cleaning_definition_id=version.cleaning_definition_id
                 WHERE definition.cleaning_key='adjusted_ohlc'
                   AND version.version_number=1
                """
            )
        )
        calendar_version_id = connection.scalar(
            text(
                "SELECT calendar_version_id FROM catalog.calendar_version "
                "WHERE artifact_id=:artifact"
            ),
            {"artifact": calendar.artifact_id},
        )
    if (
        len(securities) != len(symbols)
        or not isinstance(cleaning_version_id, uuid.UUID)
        or not isinstance(calendar_version_id, uuid.UUID)
    ):
        raise AssertionError("Trainable fixture Security or cleaning identities are missing")
    security_by_symbol = {
        str(item["security_key"]).upper(): item["security_id"] for item in securities
    }
    membership_csv = (
        "Date,tickers\n"
        f'{warmup_start.isoformat()},"{",".join(risk_symbols)}"\n'
        f'{evaluation_end.isoformat()},"{",".join(risk_symbols)}"\n'
        f'{post_end.isoformat()},"{",".join(risk_symbols)}"\n'
    )
    benchmark_membership_csv = (
        "Date,tickers\n"
        f'{warmup_start.isoformat()},"SPY"\n'
        f'{evaluation_end.isoformat()},"SPY"\n'
        f'{post_end.isoformat()},"SPY"\n'
    )
    membership_bytes = membership_csv.encode()
    membership_hash = hashlib.sha256(membership_bytes).hexdigest()
    benchmark_membership_bytes = benchmark_membership_csv.encode()
    benchmark_membership_hash = hashlib.sha256(benchmark_membership_bytes).hexdigest()
    imported = ExternalImportManifestService(engine).publish(
        ExternalImportManifestSpec(
            manifest_key="v022_trainable_runtime_fixture",
            version_number=1,
            source_project_key="v022_integration",
            source_release_key="v022_trainable_runtime_fixture_v1",
            objects=(
                ExternalImportObjectSpec(
                    object_role="membership_source",
                    logical_key="v022_trainable_membership",
                    media_type="text/csv",
                    content_sha256=membership_hash,
                    size_bytes=len(membership_bytes),
                    source_uri=f"content://sha256/{membership_hash}",
                    license_key="test_fixture",
                    provenance_status="verified",
                    usage_scope="redistributable",
                    metadata={"columns": ["Date", "tickers"]},
                ),
                ExternalImportObjectSpec(
                    object_role="benchmark_membership_source",
                    logical_key="v022_trainable_benchmark_membership",
                    media_type="text/csv",
                    content_sha256=benchmark_membership_hash,
                    size_bytes=len(benchmark_membership_bytes),
                    source_uri=f"content://sha256/{benchmark_membership_hash}",
                    license_key="test_fixture",
                    provenance_status="verified",
                    usage_scope="redistributable",
                    metadata={"columns": ["Date", "tickers"]},
                ),
            ),
            created_by="runtime_supervised_candidate",
        )
    )
    history = HistoricalSp500UniversePublicationService(engine).publish(
        HistoricalSp500UniverseSpec(
            external_import_manifest_artifact_id=imported.artifact_id,
            source_object_logical_key="v022_trainable_membership",
            universe_key="v022_trainable_runtime_universe",
            version_number=1,
            methodology_key=risk_methodology_key,
            methodology_version=1,
            research_tier="rankable_research",
            snapshots=parse_fja_snapshot_csv(membership_csv),
            mappings=tuple(
                MembershipSecurityMapping(symbol, security_by_symbol[symbol])
                for symbol in risk_symbols
            ),
            data_cutoff_at=datetime(2023, 1, 3, 2, tzinfo=UTC),
            published_at=datetime(2023, 1, 3, 3, tzinfo=UTC),
            created_by="runtime_supervised_candidate",
        )
    )
    benchmark_history = HistoricalSp500UniversePublicationService(engine).publish(
        HistoricalSp500UniverseSpec(
            external_import_manifest_artifact_id=imported.artifact_id,
            source_object_logical_key="v022_trainable_benchmark_membership",
            universe_key="v022_trainable_runtime_benchmark_universe",
            version_number=1,
            methodology_key="benchmark_membership",
            methodology_version=1,
            research_tier="rankable_research",
            snapshots=parse_fja_snapshot_csv(benchmark_membership_csv),
            mappings=(MembershipSecurityMapping("SPY", security_by_symbol["SPY"]),),
            data_cutoff_at=datetime(2023, 1, 3, 2, tzinfo=UTC),
            published_at=datetime(2023, 1, 3, 3, tzinfo=UTC),
            created_by="runtime_supervised_candidate",
        )
    )
    with engine.connect() as connection:
        ledger_id = connection.scalar(
            text(
                "SELECT universe_membership_ledger_id FROM "
                "catalog.v022_universe_history_ledger_binding "
                "WHERE universe_history_id=:history"
            ),
            {"history": history.universe_history_id},
        )
    if not isinstance(ledger_id, uuid.UUID):
        raise AssertionError("Trainable fixture Universe Ledger is missing")
    identities = ProviderSecurityIdentityService(engine)
    for symbol in symbols:
        identities.register(
            security_id=security_by_symbol[symbol],
            provider_scope="yahoo_yfinance",
            provider_symbol=symbol,
            valid_from=warmup_start,
            valid_to=None,
        )
    contract = YahooEquityContractService(engine).publish(
        load_yahoo_equity_contract(YAHOO_CONTRACT)
    )
    plan = YahooIngestionPlanService(engine).publish(
        YahooIngestionPlanSpec(
            plan_key="v022_trainable_runtime_market",
            version_number=1,
            universe_history_id=history.universe_history_id,
            data_series_version_id=contract.data_series_version_id,
            coverage_start=warmup_start,
            coverage_end=post_end,
            created_by="runtime_supervised_candidate",
        )
    )
    fetched = YahooIngestionExecutionService(
        engine,
        _FrozenYahooAdapter(sessions),
        clock=lambda: datetime(2023, 1, 3, 4, tzinfo=UTC),
    ).execute_pending(plan.yahoo_ingestion_plan_id)
    assert all(item.status == "fetched" for item in fetched)
    benchmark_plan = YahooIngestionPlanService(engine).publish(
        YahooIngestionPlanSpec(
            plan_key="v022_trainable_runtime_benchmark",
            version_number=1,
            universe_history_id=benchmark_history.universe_history_id,
            data_series_version_id=contract.data_series_version_id,
            coverage_start=warmup_start,
            coverage_end=post_end,
            created_by="runtime_supervised_candidate",
        )
    )
    benchmark_fetched = YahooIngestionExecutionService(
        engine,
        _FrozenYahooAdapter(sessions),
        clock=lambda: datetime(2023, 1, 3, 4, 1, tzinfo=UTC),
    ).execute_pending(benchmark_plan.yahoo_ingestion_plan_id)
    assert all(item.status == "fetched" for item in benchmark_fetched)
    market = SecurityMarketDataPublicationService(engine).publish(
        SecurityMarketPublicationSpec(
            yahoo_ingestion_plan_id=plan.yahoo_ingestion_plan_id,
            calendar_artifact_id=calendar.artifact_id,
            cleaning_version_id=cleaning_version_id,
            dataset_key="us_etf_daily_market_canonical",
            version_number=1,
            research_tier="rankable_research",
            created_by="runtime_supervised_candidate",
        )
    )
    if (
        market.dataset_publication_id is None
        or market.dataset_artifact_id is None
        or market.error_count != 0
    ):
        raise AssertionError("Trainable fixture market Dataset failed publication")
    benchmark_market = SecurityMarketDataPublicationService(engine).publish(
        SecurityMarketPublicationSpec(
            yahoo_ingestion_plan_id=benchmark_plan.yahoo_ingestion_plan_id,
            calendar_artifact_id=calendar.artifact_id,
            cleaning_version_id=cleaning_version_id,
            dataset_key="us_etf_daily_market_benchmark",
            version_number=1,
            research_tier="rankable_research",
            created_by="runtime_supervised_candidate",
        )
    )
    if (
        benchmark_market.dataset_publication_id is None
        or benchmark_market.dataset_artifact_id is None
        or benchmark_market.error_count != 0
    ):
        raise AssertionError("Trainable fixture benchmark Dataset failed publication")
    gate = DatasetGateAssessmentService(engine).publish(
        DatasetGateAssessmentSpec(
            dataset_publication_id=market.dataset_publication_id,
            universe_membership_ledger_id=ledger_id,
            gate_key="v022_trainable_runtime_gate",
            version_number=1,
            assessed_coverage_start=warmup_start,
            assessed_coverage_end=evaluation_end,
            ranking_eligibility="rankable_research",
            product_eligibility="eligible_with_warnings",
            evidence=(DatasetGateEvidenceRef(imported.artifact_id, "supporting_evidence"),),
            findings=(
                DatasetGateFinding(
                    finding_code="historical_membership_retrospective",
                    finding_category="membership",
                    severity="warning",
                    ranking_effect="none",
                    product_effect="warning",
                    evidence_artifact_id=imported.artifact_id,
                ),
                DatasetGateFinding(
                    finding_code="retrospective_price_snapshot",
                    finding_category="data_provenance",
                    severity="warning",
                    ranking_effect="none",
                    product_effect="warning",
                ),
            ),
            uniform_exclusions=(),
            created_by="runtime_supervised_candidate",
        )
    )
    cohort = EvaluationCohortPublicationService(engine).publish(
        EvaluationCohortSpec(
            cohort_key=frozen_sp500_cohort_key(frequency),
            version_number=FROZEN_SP500_COHORT_VERSION,
            research_tier="rankable_research",
            frequency=frequency,
            universe_history_id=history.universe_history_id,
            dataset_publication_id=market.dataset_publication_id,
            benchmark_dataset_publication_id=benchmark_market.dataset_publication_id,
            security_market_quality_report_id=market.quality_report_id,
            calendar_version_id=calendar_version_id,
            warmup_start=warmup_start,
            evaluation_start=evaluation_start,
            evaluation_end=evaluation_end,
            cost_bps_per_side=Decimal("5"),
            created_by="runtime_supervised_candidate",
        )
    )
    CohortRuntimeContractService(engine).publish(
        evaluation_cohort_version_id=cohort.evaluation_cohort_version_id,
        dataset_gate_assessment_id=gate.dataset_gate_assessment_id,
        created_by="runtime_supervised_candidate",
    )
    snapshots = SourceSnapshotService(engine)
    rate_rows = ["observation_date,DGS3MO"]
    current = warmup_start - timedelta(days=3)
    while current <= post_end:
        rate_rows.append(f"{current.isoformat()},4.00")
        current += timedelta(days=1)
    rate_artifact = _source_snapshot(
        snapshots,
        "DGS3MO",
        ("\n".join(rate_rows) + "\n").encode(),
        datetime(2023, 1, 3, 5, tzinfo=UTC),
    )
    canonical = CanonicalDataPublicationService(engine)
    rate = canonical.publish_rate(rate_artifact, version_number=1)
    _, reserve_model = publish_reserve_model(engine)
    reserve = ReservePublicationService(engine).publish(
        rate.artifact_id,
        calendar.artifact_id,
        reserve_model.artifact_id,
        version_number=1,
    )
    publish_data_bundle(
        engine,
        benchmark_market.dataset_artifact_id,
        rate.artifact_id,
        reserve.artifact_id,
        calendar.artifact_id,
        version_number=1,
        market_dataset_key="us_etf_daily_market_benchmark",
    )
    return (
        cohort.evaluation_cohort_version_id,
        tuple(security_by_symbol[symbol] for symbol in risk_symbols),
        evaluation_start,
        evaluation_end,
    )


def _patch_fixture_active_identity(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    evaluation_cohort_version_id: uuid.UUID,
) -> None:
    """Bind the compact integration Dataset to the hardened workspace resolver.

    Production discovers the sole complete v5/Registry 0.22.4 environment.  This
    test intentionally publishes a five-Security synthetic Dataset instead, so it
    supplies the equivalent immutable identities without weakening production
    discovery or manufacturing the 626-profile production Registry.
    """

    with engine.connect() as connection:
        row = (
            connection.execute(
                text(
                    """
                SELECT registry.asset_registry_release_id,
                       registry.artifact_id AS asset_registry_artifact_id,
                       registry.version_number AS asset_registry_version_number,
                       registry.catalog_version AS asset_registry_catalog_version,
                       registry.as_of_date AS asset_registry_as_of_date,
                       cohort.universe_history_id,
                       risk.dataset_publication_id AS risk_dataset_publication_id,
                       risk.artifact_id AS risk_dataset_artifact_id,
                       risk.dataset_key AS risk_dataset_key,
                       risk.version_number AS risk_dataset_version_number,
                       benchmark.dataset_publication_id AS benchmark_dataset_publication_id,
                       benchmark.artifact_id AS benchmark_dataset_artifact_id,
                       benchmark.dataset_key AS benchmark_dataset_key,
                       benchmark.version_number AS benchmark_dataset_version_number,
                       gate.dataset_gate_assessment_id,
                       gate.artifact_id AS dataset_gate_artifact_id
                  FROM experiment.v022_evaluation_cohort_version cohort
                  JOIN data.dataset_publication risk
                    ON risk.dataset_publication_id=cohort.dataset_publication_id
                  JOIN data.dataset_publication benchmark
                    ON benchmark.dataset_publication_id=
                       cohort.benchmark_dataset_publication_id
                  JOIN experiment.v022_evaluation_cohort_runtime_contract runtime
                    ON runtime.evaluation_cohort_version_id=
                       cohort.evaluation_cohort_version_id
                  JOIN data.v022_dataset_gate_assessment gate
                    ON gate.dataset_gate_assessment_id=
                       runtime.dataset_gate_assessment_id
                  CROSS JOIN LATERAL (
                    SELECT release.asset_registry_release_id,release.artifact_id,
                           release.version_number,release.catalog_version,
                           release.as_of_date
                      FROM catalog.asset_registry_release release
                     ORDER BY release.version_number DESC
                     LIMIT 1
                  ) registry
                 WHERE cohort.evaluation_cohort_version_id=:cohort
                """
                ),
                {"cohort": evaluation_cohort_version_id},
            )
            .mappings()
            .one()
        )
    identity = ActiveV022WorkspaceIdentity(**dict(row))
    monkeypatch.setattr(
        workspace_context,
        "require_active_v022_workspace_identity",
        lambda _connection: identity,
    )
    monkeypatch.setattr(
        "style_rotation.v022.draft_service.require_active_v022_workspace_identity",
        lambda _connection: identity,
    )


def _source_snapshot(
    service: SourceSnapshotService,
    subject: str,
    payload: bytes,
    fetched_at: datetime,
) -> uuid.UUID:
    market = subject != "DGS3MO"
    return service.publish(
        SnapshotInput(
            series_key="us_etf_daily_market" if market else "dgs3mo_daily",
            series_version=1,
            snapshot_key=f"v022-runtime-{subject.lower()}-{fetched_at:%Y%m%dT%H%M%S%fZ}",
            requested_at=fetched_at - timedelta(seconds=1),
            fetched_at=fetched_at,
            as_of_at=fetched_at,
            media_type="text/csv",
            request_parameters={"tickers": subject} if market else {"id": subject},
            response_metadata={"fixture": True},
            raw_payload=payload,
        )
    ).artifact_id
