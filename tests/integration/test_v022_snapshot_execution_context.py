from __future__ import annotations

import os
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.engine import make_url

import style_rotation.v022.draft_service as draft_service_module
import style_rotation.v022.workspace_context as workspace_context
from style_rotation.catalog.asset_registry import (
    publish_asset_identities,
    publish_asset_registry,
)
from style_rotation.catalog.scope import publish_research_scope
from style_rotation.data.bundle import ReservePublicationService, publish_reserve_model
from style_rotation.data.calendar import CalendarPublicationService, XNYSCalendarGenerator
from style_rotation.data.publication import CanonicalDataPublicationService
from style_rotation.data.service import (
    SnapshotInput,
    SourceSnapshotService,
    publish_data_contracts,
)
from style_rotation.persistence.database import (
    database_status,
    downgrade_database,
    reset_database,
)
from style_rotation.persistence.session import create_postgres_engine
from style_rotation.v022.draft_service import GraphDraftService
from style_rotation.v022.experiment_identity import ConfigurationSnapshotService
from style_rotation.v022.publication import (
    CatalogPublicationContext,
    publish_catalog_release,
)
from style_rotation.v022.suite_identity import (
    EXPLORATORY_EXECUTION_POLICY,
    GraphSuiteIdentityService,
)
from style_rotation.v022.workspace_context import ActiveV022WorkspaceIdentity
from style_rotation.v022.workspace_view import GraphWorkspacePreviewService

pytestmark = pytest.mark.integration
DATABASE_URL = os.getenv("STYLE_ROTATION_TEST_DATABASE_URL")
PROJECT_ROOT = Path(__file__).parents[2]
MANIFEST = (
    PROJECT_ROOT
    / "v0.22"
    / "catalogs"
    / "releases"
    / "catalog_release.v0.22.5.json"
)
RESERVE_MODEL = PROJECT_ROOT / "v0.2/catalogs/reserve_model.v0.2.0.json"
CONTEXT = CatalogPublicationContext(
    actor_key="local_researcher",
    reviewer_actor="local_researcher",
    trusted_local_authorization_bootstrap=True,
)


@pytest.fixture(autouse=True)
def _use_fixture_workspace_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bind this focused ETF fixture to the strict active-context seam."""

    def resolve(connection) -> ActiveV022WorkspaceIdentity:
        registry = connection.execute(
            text(
                "SELECT asset_registry_release_id,artifact_id,version_number,"
                "catalog_version,as_of_date FROM catalog.asset_registry_release "
                "ORDER BY version_number DESC LIMIT 1"
            )
        ).mappings().one()
        dataset = connection.execute(
            text(
                "SELECT dataset_publication_id,artifact_id,dataset_key,version_number "
                "FROM data.dataset_publication WHERE value_kind='daily_bar' "
                "ORDER BY version_number DESC LIMIT 1"
            )
        ).mappings().one()
        return ActiveV022WorkspaceIdentity(
            asset_registry_release_id=registry["asset_registry_release_id"],
            asset_registry_artifact_id=registry["artifact_id"],
            asset_registry_version_number=registry["version_number"],
            asset_registry_catalog_version=registry["catalog_version"],
            asset_registry_as_of_date=registry["as_of_date"],
            universe_history_id=uuid.UUID(int=1),
            risk_dataset_publication_id=dataset["dataset_publication_id"],
            risk_dataset_artifact_id=dataset["artifact_id"],
            risk_dataset_key=dataset["dataset_key"],
            risk_dataset_version_number=dataset["version_number"],
            benchmark_dataset_publication_id=dataset["dataset_publication_id"],
            benchmark_dataset_artifact_id=dataset["artifact_id"],
            benchmark_dataset_key=dataset["dataset_key"],
            benchmark_dataset_version_number=dataset["version_number"],
            dataset_gate_assessment_id=uuid.UUID(int=2),
            dataset_gate_artifact_id=uuid.UUID(int=3),
        )

    monkeypatch.setattr(
        workspace_context,
        "require_active_v022_workspace_identity",
        resolve,
    )
    monkeypatch.setattr(
        draft_service_module,
        "require_active_v022_workspace_identity",
        resolve,
    )


@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_snapshot_and_suite_bind_exact_no_defense_execution_context() -> None:
    assert DATABASE_URL is not None
    database_name = make_url(DATABASE_URL).database
    assert database_name is not None
    reset_database(DATABASE_URL, database_name, "test")
    engine = create_postgres_engine(DATABASE_URL)
    try:
        _publish_context_inputs(engine)
        publish_catalog_release(engine, MANIFEST, context=CONTEXT)
        drafts = GraphDraftService(
            engine,
            GraphWorkspacePreviewService.from_manifest(MANIFEST),
        )
        draft = drafts.create(
            researcher_key="snapshot_context_researcher",
            draft_key="snapshot_context_composed_v5",
            name="Snapshot execution context integration fixture",
            idempotency_key=uuid.uuid4(),
            asset_context_key="us_style_rotation_4_etf_sample_v1",
            data_input_keys=("canonical_market_bars",),
        )
        selected = drafts.apply_event(
            draft.graph_draft_id,
            expected_revision=draft.revision,
            actor_key="snapshot_context_researcher",
            idempotency_key=uuid.uuid4(),
            event_type="select_feature_occurrence",
            event={"feature_key": "return_continuation__w120", "stage_no": 3},
        ).snapshot
        events: tuple[tuple[str, dict[str, object]], ...] = (
            ("select_aggregation_family", {"family_key": "flat_equal_weight_mean"}),
            (
                "set_aggregation_parameter_presets",
                {
                    "family_key": "flat_equal_weight_mean",
                    "preset_keys": ["signal_equal_v1"],
                },
            ),
            ("select_strategy", {"strategy_key": "cross_section_rank_top_k_parity"}),
            (
                "set_strategy_parameter_presets",
                {
                    "strategy_key": "cross_section_rank_top_k_parity",
                    "preset_keys": ["k1"],
                },
            ),
            ("select_defense", {"defense_key": "none"}),
        )
        for event_type, event in events:
            selected = drafts.apply_event(
                draft.graph_draft_id,
                expected_revision=selected.revision,
                actor_key="snapshot_context_researcher",
                idempotency_key=uuid.uuid4(),
                event_type=event_type,
                event=event,
            ).snapshot
        compiled = drafts.compile(
            draft.graph_draft_id,
            expected_revision=selected.revision,
            actor_key="snapshot_context_researcher",
            idempotency_key=uuid.uuid4(),
        )
        assert compiled.compiled_execution_data_context_id is not None
        assert compiled.defense_execution_contexts == ()

        submission_key = uuid.uuid4()
        suites = GraphSuiteIdentityService(engine)
        suite = suites.publish(
            compiled_research_graph_id=compiled.compiled_research_graph_id,
            submission_key=submission_key,
            actor_key="snapshot_context_researcher",
        )
        suite_replay = suites.publish(
            compiled_research_graph_id=compiled.compiled_research_graph_id,
            submission_key=submission_key,
            actor_key="snapshot_context_researcher",
        )
        assert suite_replay.reused is True
        assert suite_replay.research_suite_id == suite.research_suite_id

        with engine.connect() as connection:
            rows = tuple(
                connection.execute(
                    text(
                        """
                        SELECT branch.compiled_strategy_branch_id,
                               branch.defense_version_id,snapshot.artifact_id,
                               snapshot.semantic_identity_document,
                               binding.*
                          FROM experiment.v022_research_suite_branch suite_branch
                          JOIN strategy.v022_compiled_strategy_branch branch
                            ON branch.compiled_strategy_branch_id=
                               suite_branch.compiled_strategy_branch_id
                          JOIN experiment.v022_research_configuration_snapshot snapshot
                            ON snapshot.configuration_snapshot_id=
                               suite_branch.configuration_snapshot_id
                          JOIN experiment.v022_configuration_execution_context_binding binding
                            ON binding.configuration_snapshot_id=
                               snapshot.configuration_snapshot_id
                         WHERE suite_branch.research_suite_id=:suite
                         ORDER BY suite_branch.ordinal
                        """
                    ),
                    {"suite": suite.research_suite_id},
                ).mappings()
            )
            assert len(rows) == suite.strategy_branch_count
            assert len(rows) == 1
            none_row = rows[0]
            assert all(
                row["compiled_execution_data_context_id"]
                == compiled.compiled_execution_data_context_id
                for row in rows
            )
            assert none_row["compiled_defense_execution_context_id"] is None
            assert none_row["binding_document"]["defense"] is None
            assert none_row["defense_version_id"] is None
            for row in rows:
                assert (
                    row["semantic_identity_document"]["execution_contexts"]
                    == row["binding_document"]
                )
                roles = tuple(
                    connection.scalars(
                        text(
                            "SELECT role FROM lineage.artifact_dependency "
                            "WHERE artifact_id=:artifact ORDER BY role,ordinal"
                        ),
                        {"artifact": row["artifact_id"]},
                    )
                )
                expected = (
                    "compiled_execution_data_context",
                    "compiled_graph",
                    "strategy_parameter_preset",
                )
                assert roles == tuple(sorted(expected))

        configurations = ConfigurationSnapshotService(engine)
        replay = configurations.publish(
            compiled_strategy_branch_id=none_row["compiled_strategy_branch_id"],
            execution_policy_document=EXPLORATORY_EXECUTION_POLICY,
            provenance_document={"source": "replay_must_not_replace_frozen_identity"},
            compiled_execution_data_context_id=(
                compiled.compiled_execution_data_context_id
            ),
        )
        assert replay.reused is True
        assert replay.execution_context_binding == none_row["binding_document"]

        with pytest.raises(ValueError, match="exact Risk Execution Context"):
            configurations.publish(
                compiled_strategy_branch_id=none_row[
                    "compiled_strategy_branch_id"
                ],
                execution_policy_document={"policy_key": "missing_context_negative"},
                provenance_document={"source": "negative"},
            )
        with pytest.raises(LookupError, match="Risk Execution Context not found"):
            configurations.publish(
                compiled_strategy_branch_id=none_row[
                    "compiled_strategy_branch_id"
                ],
                execution_policy_document={"policy_key": "wrong_risk_negative"},
                provenance_document={"source": "negative"},
                compiled_execution_data_context_id=uuid.uuid4(),
            )
        with pytest.raises(ValueError, match="No-defense.*forbids"):
            configurations.publish(
                compiled_strategy_branch_id=none_row["compiled_strategy_branch_id"],
                execution_policy_document={"policy_key": "wrong_defense_negative"},
                provenance_document={"source": "negative"},
                compiled_execution_data_context_id=(
                    compiled.compiled_execution_data_context_id
                ),
                compiled_defense_execution_context_id=uuid.uuid4(),
            )
        with pytest.raises(
            Exception,
            # The current schema's Research Round identity is the outermost
            # immutable owner of this Draft and therefore blocks the multi-
            # revision downgrade before the older Snapshot Context guard is
            # reached.  The migration-specific precondition is covered in the
            # database-foundation integration tests.
            match="Cannot downgrade nonempty v0.22 Research Round identities",
        ):
            downgrade_database(DATABASE_URL, "20260812_77_v022_defense_package")
        assert database_status(DATABASE_URL).current_revision == (
            "20260821_142_asset_export"
        )
    finally:
        engine.dispose()


def _publish_context_inputs(engine: Engine) -> None:
    publish_research_scope(
        engine, PROJECT_ROOT / "v0.2/catalogs/research_scope.v0.2.0.json"
    )
    asset_catalog = PROJECT_ROOT / "v0.21/catalogs/assets.v0.21.1.json"
    publish_asset_registry(engine, asset_catalog)
    publish_asset_identities(engine, asset_catalog)
    publish_data_contracts(
        engine, PROJECT_ROOT / "v0.2/catalogs/data_contracts.v0.2.0.json"
    )
    generated = XNYSCalendarGenerator().generate(
        date(2026, 7, 27), date(2026, 7, 31)
    )
    calendar = CalendarPublicationService(engine).publish(generated)
    snapshots = SourceSnapshotService(engine)
    fetched = datetime(2026, 8, 3, 1, 0, tzinfo=UTC)
    header = "session_date,Open,High,Low,Close,Adj Close,Volume,Dividends,Stock Splits\n"
    market_artifacts: list[uuid.UUID] = []
    symbols = ("IWF", "IWD", "IWO", "IWN", "SPY", "IEF", "TLT", "TIP", "IAU")
    for ordinal, symbol in enumerate(symbols):
        rows = "".join(
            f"{session.session_date},100,102,99,101,101,1000,0,0\n"
            for session in generated.sessions
        )
        market_artifacts.append(
            _source_snapshot(
                snapshots,
                symbol,
                (header + rows).encode(),
                fetched + timedelta(microseconds=ordinal),
            )
        )
    rate_rows = "observation_date,DGS3MO\n" + "".join(
        f"{date(2026, 7, 24) + timedelta(days=offset)},4.00\n"
        for offset in range(8)
    )
    rate_artifact = _source_snapshot(
        snapshots,
        "DGS3MO",
        rate_rows.encode(),
        fetched + timedelta(microseconds=20),
    )
    canonical = CanonicalDataPublicationService(engine)
    canonical.publish_market(
        tuple(market_artifacts), calendar.artifact_id, version_number=1
    )
    rate = canonical.publish_rate(rate_artifact, version_number=1)
    _, reserve_model = publish_reserve_model(engine, RESERVE_MODEL)
    ReservePublicationService(engine).publish(
        rate.artifact_id,
        calendar.artifact_id,
        reserve_model.artifact_id,
        version_number=1,
    )


def _source_snapshot(
    service: SourceSnapshotService,
    subject: str,
    payload: bytes,
    fetched_at: datetime,
) -> uuid.UUID:
    market = subject != "DGS3MO"
    result = service.publish(
        SnapshotInput(
            series_key="us_etf_daily_market" if market else "dgs3mo_daily",
            series_version=1,
            snapshot_key=(
                f"v022-snapshot-context-{subject.lower()}-"
                f"{fetched_at:%Y%m%dT%H%M%S%fZ}"
            ),
            requested_at=fetched_at - timedelta(seconds=1),
            fetched_at=fetched_at,
            as_of_at=fetched_at,
            media_type="text/csv",
            request_parameters={"tickers": subject} if market else {"id": subject},
            response_metadata={"fixture": True},
            raw_payload=payload,
        )
    )
    return result.artifact_id
