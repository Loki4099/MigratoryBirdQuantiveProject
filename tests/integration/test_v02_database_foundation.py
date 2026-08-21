from __future__ import annotations

import hashlib
import json
import os
import uuid

import psycopg
import pytest
from sqlalchemy.engine import make_url

from style_rotation.persistence.base import SCHEMA_NAMES
from style_rotation.persistence.database import (
    database_status,
    downgrade_database,
    reset_database,
    upgrade_database,
)

DATABASE_URL = os.getenv("STYLE_ROTATION_TEST_DATABASE_URL")


def _reset() -> str:
    assert DATABASE_URL is not None
    database_name = make_url(DATABASE_URL).database
    assert database_name is not None
    reset_database(DATABASE_URL, database_name, "test")
    return DATABASE_URL.replace("postgresql+psycopg://", "postgresql://", 1)


def _insert_published(
    connection: psycopg.Connection[tuple[object, ...]],
    artifact_id: uuid.UUID,
    artifact_key: str,
    semantic_fingerprint: str,
    content_hash: str,
) -> None:
    connection.execute(
        """
        INSERT INTO lineage.artifact (
            artifact_id, artifact_type, artifact_key, version_number, status
        ) VALUES (%s, 'test', %s, 1, 'draft')
        """,
        (artifact_id, artifact_key),
    )
    connection.execute(
        """
        SELECT set_config('style_rotation.status_event_id', %s, true),
               set_config('style_rotation.status_reason', 'test publication', true)
        """,
        (str(uuid.uuid4()),),
    )
    connection.execute(
        """
        UPDATE lineage.artifact
        SET status = 'published', semantic_fingerprint = %s, content_hash = %s,
            published_at = now()
        WHERE artifact_id = %s
        """,
        (semantic_fingerprint, content_hash, artifact_id),
    )


def _fingerprint(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _publish_merge_output_fixture(
    connection: psycopg.Connection[tuple[object, ...]],
    *,
    case_key: str,
    logical_payload_fingerprint: str,
    failure_mode: str | None = None,
) -> None:
    ids = {key: uuid.uuid4() for key in (
        "source_artifact", "source_work", "source_output", "source_manifest",
        "source_manifest_artifact", "work", "spec", "plan", "branch",
        "compiled_branch", "snapshot", "contract", "encoding", "artifact",
        "manifest_artifact", "manifest", "object", "partition", "dependency",
        "manifest_dependency", "output",
    )}
    execution_fingerprint = _fingerprint(f"{case_key}:execution")
    source_execution_fingerprint = _fingerprint(f"{case_key}:source-execution")
    output_semantic_fingerprint = _fingerprint(f"{case_key}:output-semantic")
    source_semantic_fingerprint = _fingerprint(f"{case_key}:source-semantic")
    manifest_semantic_fingerprint = _fingerprint(f"{case_key}:manifest-semantic")
    manifest_hash = _fingerprint(f"{case_key}:manifest")
    object_hash = _fingerprint(f"{case_key}:object")
    descriptor_hash = _fingerprint(f"{case_key}:partition")
    connection.execute("SET session_replication_role=replica")
    for artifact_id, artifact_type, artifact_key, semantic_fingerprint in (
        (
            ids["source_artifact"],
            "m79_test_strategy_target",
            f"m79-source-{case_key}",
            source_semantic_fingerprint,
        ),
        (
            ids["artifact"],
            "v022_merged_portfolio_target_path",
            f"v022_merged_portfolio_target_path__{execution_fingerprint}",
            output_semantic_fingerprint,
        ),
        (
            ids["manifest_artifact"],
            "v022_payload_manifest",
            f"v022_payload_manifest__{manifest_hash}",
            manifest_semantic_fingerprint,
        ),
    ):
        connection.execute(
            """
            INSERT INTO lineage.artifact (
              artifact_id,artifact_type,artifact_key,version_number,status,
              semantic_fingerprint,content_hash,published_at
            ) VALUES (%s,%s,%s,1,'published',%s,%s,now())
            """,
            (
                artifact_id,
                artifact_type,
                artifact_key,
                semantic_fingerprint,
                _fingerprint(f"{case_key}:{artifact_type}:content"),
            ),
        )
    connection.execute(
        """
        INSERT INTO workspace.v022_graph_work_item (
          graph_work_item_id,execution_fingerprint,work_kind,status,priority,
          lease_owner,lease_expires_at,lease_generation,fencing_token,attempt_count
        ) VALUES (%s,%s,'sleeve_merge','running',100,'m79-worker',
                  now()+interval '5 minutes',1,7,1)
        """,
        (ids["work"], execution_fingerprint),
    )
    connection.execute(
        """
        INSERT INTO strategy.v022_strategy_target_path (
          strategy_target_path_id,artifact_id,graph_work_item_id,work_kind,
          payload_manifest_id,payload_manifest_artifact_id,manifest_hash,
          work_execution_fingerprint,logical_payload_fingerprint,output_fingerprint,
          artifact_semantic_fingerprint,decision_count,target_document,worker_key,
          fencing_token
        ) VALUES (%s,%s,%s,'strategy_target',%s,%s,%s,%s,%s,%s,%s,1,
                  '{"fixture":true}'::jsonb,'source-worker',1)
        """,
        (
            ids["source_output"], ids["source_artifact"], ids["source_work"],
            ids["source_manifest"], ids["source_manifest_artifact"],
            _fingerprint(f"{case_key}:source-manifest"), source_execution_fingerprint,
            _fingerprint(f"{case_key}:source-logical"), source_execution_fingerprint,
            source_semantic_fingerprint,
        ),
    )
    connection.execute(
        """
        INSERT INTO strategy.v022_sleeve_merge_work_spec (
          sleeve_merge_work_spec_id,graph_work_item_id,work_kind,suite_runtime_plan_id,
          research_suite_branch_id,compiled_strategy_branch_id,
          configuration_snapshot_id,output_payload_contract_version_id,
          physical_encoding_version_id,source_strategy_work_item_id,
          source_defense_work_item_id,occurrence_key,specification_document,
          specification_fingerprint,plan_artifact_semantic_fingerprint
        ) VALUES (%s,%s,'sleeve_merge',%s,%s,%s,%s,%s,%s,%s,NULL,%s,
                  '{"fixture":true}'::jsonb,%s,%s)
        """,
        (
            ids["spec"], ids["work"], ids["plan"], ids["branch"],
            ids["compiled_branch"], ids["snapshot"], ids["contract"],
            ids["encoding"], ids["source_work"], f"merge:{case_key}",
            execution_fingerprint, _fingerprint(f"{case_key}:plan-semantic"),
        ),
    )
    verification_status = "pending" if failure_mode == "unverified" else "verified"
    verified_at = "NULL" if failure_mode == "unverified" else "now()"
    connection.execute(
        f"""
        INSERT INTO data.payload_object (
          payload_object_id,object_content_hash,storage_uri,byte_size,object_state,
          verification_status,verified_at
        ) VALUES (%s,%s,%s,10,'published',%s,{verified_at})
        """,
        (
            ids["object"], object_hash,
            f"payload-object://sha256/{object_hash}.parquet", verification_status,
        ),
    )
    connection.execute(
        """
        INSERT INTO data.payload_partition (
          payload_partition_id,payload_object_id,partition_descriptor_hash,byte_size,
          row_or_item_count,partition_key,coverage_document,statistics
        ) VALUES (%s,%s,%s,10,1,'{}'::jsonb,'{}'::jsonb,'{}'::jsonb)
        """,
        (ids["partition"], ids["object"], descriptor_hash),
    )
    manifest_byte_size = 11 if failure_mode == "bad_sum" else 10
    connection.execute(
        """
        INSERT INTO data.payload_manifest (
          payload_manifest_id,artifact_id,payload_contract_version_id,
          physical_encoding_version_id,producer_artifact_id,producer_output_port_key,
          logical_payload_fingerprint,manifest_hash,partition_count,byte_size,
          row_or_item_count,coverage_document,retention_class,materialization_state
        ) VALUES (%s,%s,%s,%s,%s,'merged_portfolio_target',%s,%s,1,%s,1,
                  '{}'::jsonb,'research','materialized')
        """,
        (
            ids["manifest"], ids["manifest_artifact"], ids["contract"],
            ids["encoding"], ids["artifact"], logical_payload_fingerprint,
            manifest_hash, manifest_byte_size,
        ),
    )
    if failure_mode != "empty":
        connection.execute(
            """
            INSERT INTO data.payload_manifest_partition (
              payload_manifest_id,payload_partition_id,ordinal
            ) VALUES (%s,%s,0)
            """,
            (ids["manifest"], ids["partition"]),
        )
    connection.execute(
        """
        INSERT INTO lineage.artifact_dependency (
          artifact_dependency_id,artifact_id,depends_on_artifact_id,role,ordinal
        ) VALUES (%s,%s,%s,'strategy_target',0),
                (%s,%s,%s,'producer',0)
        """,
        (
            ids["dependency"], ids["artifact"], ids["source_artifact"],
            ids["manifest_dependency"], ids["manifest_artifact"], ids["artifact"],
        ),
    )
    connection.execute("SET session_replication_role=origin")
    connection.execute(
        """
        INSERT INTO strategy.v022_merged_portfolio_target_path (
          merged_portfolio_target_path_id,artifact_id,graph_work_item_id,work_kind,
          payload_manifest_id,payload_manifest_artifact_id,manifest_hash,
          work_execution_fingerprint,logical_payload_fingerprint,output_fingerprint,
          artifact_semantic_fingerprint,decision_count,target_document,worker_key,
          fencing_token
        ) VALUES (%s,%s,%s,'sleeve_merge',%s,%s,%s,%s,%s,%s,%s,1,
                  '{"fixture":true}'::jsonb,'m79-worker',7)
        """,
        (
            ids["output"], ids["artifact"], ids["work"], ids["manifest"],
            ids["manifest_artifact"], manifest_hash, execution_fingerprint,
            logical_payload_fingerprint, execution_fingerprint,
            output_semantic_fingerprint,
        ),
    )
    connection.execute(
        """
        UPDATE workspace.v022_graph_work_item
           SET status='completed',lease_owner=NULL,lease_expires_at=NULL,updated_at=now()
         WHERE graph_work_item_id=%s
        """,
        (ids["work"],),
    )


@pytest.mark.integration
@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_empty_database_migrates_to_one_clean_v022_m1_head() -> None:
    psycopg_url = _reset()
    assert DATABASE_URL is not None
    status = database_status(DATABASE_URL)
    assert status.current_revision == "20260821_142_asset_export"
    assert status.head_revisions == ("20260821_142_asset_export",)
    assert status.present_schemas == SCHEMA_NAMES
    assert status.missing_schemas == ()

    with psycopg.connect(psycopg_url) as connection:
        schemas = {
            row[0]
            for row in connection.execute(
                "SELECT schema_name FROM information_schema.schemata"
            ).fetchall()
        }
        assert set(SCHEMA_NAMES).issubset(schemas)
        public_tables = connection.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        ).fetchall()
        assert public_tables == [("alembic_version",)]
        assert connection.execute("SELECT to_regclass('ops.backup_record')").fetchone() == (
            "ops.backup_record",
        )
        m7_tables = connection.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema='experiment' AND table_name IN ("
            "'v022_research_configuration_snapshot','v022_configuration_direct_input',"
            "'v022_common_evaluation_panel','v022_common_evaluation_panel_member',"
            "'v022_result_evidence_snapshot')"
        ).fetchone()
        assert m7_tables == (5,)
        suite_identity_tables = connection.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema='experiment' AND table_name IN ("
            "'v022_evaluation_matrix_policy','v022_evaluation_matrix_policy_context',"
            "'v022_research_suite','v022_research_suite_branch','v022_research_cell',"
            "'v022_research_suite_graph_run_binding')"
        ).fetchone()
        assert suite_identity_tables == (6,)
        strategy_preset_tables = connection.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema='strategy' AND table_name IN ("
            "'v022_strategy_parameter_preset_definition',"
            "'v022_strategy_parameter_preset_version',"
            "'v022_compiled_strategy_branch_preset_binding')"
        ).fetchone()
        assert strategy_preset_tables == (3,)
        execution_context_tables = connection.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema='workspace' AND table_name IN ("
            "'v022_compiled_execution_data_context',"
            "'v022_compiled_execution_data_input')"
        ).fetchone()
        assert execution_context_tables == (2,)
        explicit_selection_tables = connection.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema='workspace' AND table_name IN ("
            "'v022_explicit_asset_selection',"
            "'v022_explicit_asset_selection_member')"
        ).fetchone()
        assert explicit_selection_tables == (2,)
        context_payload_check = connection.execute(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid='data.v022_execution_context_payload_binding'::regclass "
            "AND contype='c' AND pg_get_constraintdef(oid) LIKE '%snapshot_semantics%'"
        ).fetchone()
        assert context_payload_check is not None
        assert "IS TRUE" in context_payload_check[0]
        defense_package_tables = connection.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema='defense' AND table_name IN ("
            "'v022_timing_policy_family','v022_timing_policy_variant',"
            "'v022_timing_policy_version','v022_allocation_policy_family',"
            "'v022_allocation_policy_variant','v022_allocation_policy_version',"
            "'v022_allocation_policy_member',"
            "'v022_defense_package_policy_binding',"
            "'v022_defense_package_supported_asset_set',"
            "'v022_compiled_defense_execution_context',"
            "'v022_compiled_defense_execution_data_input')"
        ).fetchone()
        assert defense_package_tables == (11,)
        snapshot_context_tables = connection.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema='experiment' AND table_name="
            "'v022_configuration_execution_context_binding'"
        ).fetchone()
        assert snapshot_context_tables == (1,)
        suite_runtime_tables = connection.execute(
            "SELECT count(*) FROM information_schema.tables WHERE "
            "(table_schema,table_name) IN ("
            "('experiment','v022_suite_runtime_plan'),"
            "('strategy','v022_strategy_target_work_spec'),"
            "('defense','v022_defense_decision_work_spec'),"
            "('strategy','v022_sleeve_merge_work_spec'),"
            "('experiment','v022_portfolio_cell_work_spec'),"
            "('strategy','v022_strategy_target_path'),"
            "('defense','v022_defense_decision_path'),"
            "('strategy','v022_merged_portfolio_target_path'),"
            "('experiment','v022_portfolio_cell_runtime_result'))"
        ).fetchone()
        assert suite_runtime_tables == (9,)
        evaluation_context_tables = connection.execute(
            "SELECT count(*) FROM information_schema.tables WHERE "
            "(table_schema,table_name) IN ("
            "('experiment','v022_portfolio_evaluation_data_context'),"
            "('experiment','v022_portfolio_evaluation_data_input'))"
        ).fetchone()
        assert evaluation_context_tables == (2,)
        assert connection.execute(
            "SELECT to_regclass("
            "'experiment.v022_research_cell_evaluation_data_context_binding')"
        ).fetchone() == (
            "experiment.v022_research_cell_evaluation_data_context_binding",
        )
        work_spec_primary_keys = tuple(
            connection.execute(
                """
                    SELECT table_schema,table_name,column_name
                      FROM information_schema.key_column_usage
                     WHERE constraint_name IN (
                       'v022_strategy_target_work_spec_pkey',
                       'v022_defense_decision_work_spec_pkey',
                       'v022_sleeve_merge_work_spec_pkey',
                       'v022_portfolio_cell_work_spec_pkey'
                     )
                     ORDER BY table_schema,table_name
                    """
            )
        )
        assert work_spec_primary_keys == (
            ("defense", "v022_defense_decision_work_spec", "defense_decision_work_spec_id"),
            ("experiment", "v022_portfolio_cell_work_spec", "portfolio_cell_work_spec_id"),
            ("strategy", "v022_sleeve_merge_work_spec", "sleeve_merge_work_spec_id"),
            ("strategy", "v022_strategy_target_work_spec", "strategy_target_work_spec_id"),
        )
        output_shape = tuple(
            connection.execute(
                """
                    SELECT table_schema,table_name,column_name
                      FROM information_schema.columns
                     WHERE (table_schema,table_name) IN (
                       ('strategy','v022_strategy_target_path'),
                       ('defense','v022_defense_decision_path'),
                       ('strategy','v022_merged_portfolio_target_path'),
                       ('experiment','v022_portfolio_cell_runtime_result')
                     ) AND column_name IN (
                       'payload_manifest_artifact_id','artifact_semantic_fingerprint',
                       'research_cell_id'
                     )
                     ORDER BY table_schema,table_name,column_name
                    """
            )
        )
        assert output_shape == (
            (
                "defense",
                "v022_defense_decision_path",
                "artifact_semantic_fingerprint",
            ),
            (
                "defense",
                "v022_defense_decision_path",
                "payload_manifest_artifact_id",
            ),
            (
                "experiment",
                "v022_portfolio_cell_runtime_result",
                "artifact_semantic_fingerprint",
            ),
            (
                "experiment",
                "v022_portfolio_cell_runtime_result",
                "payload_manifest_artifact_id",
            ),
            (
                "strategy",
                "v022_merged_portfolio_target_path",
                "artifact_semantic_fingerprint",
            ),
            (
                "strategy",
                "v022_merged_portfolio_target_path",
                "payload_manifest_artifact_id",
            ),
            (
                "strategy",
                "v022_strategy_target_path",
                "artifact_semantic_fingerprint",
            ),
            (
                "strategy",
                "v022_strategy_target_path",
                "payload_manifest_artifact_id",
            ),
        )
        portfolio_result_columns = {
            row[0]
            for row in connection.execute(
                """
                SELECT column_name FROM information_schema.columns
                 WHERE table_schema='experiment'
                   AND table_name='v022_portfolio_cell_runtime_result'
                """
            )
        }
        assert {
            "work_execution_fingerprint",
            "logical_payload_fingerprint",
            "artifact_semantic_fingerprint",
            "compiled_strategy_branch_id",
            "configuration_snapshot_id",
            "evaluation_data_context_fingerprint",
        }.issubset(portfolio_result_columns)
        assert "research_cell_id" not in portfolio_result_columns
        assert "research_suite_branch_id" not in portfolio_result_columns
        logical_payload_unique_constraints = connection.execute(
            """
            SELECT count(*)
              FROM pg_constraint constraint_row
              JOIN pg_class table_row ON table_row.oid=constraint_row.conrelid
              JOIN pg_namespace schema_row ON schema_row.oid=table_row.relnamespace
             WHERE constraint_row.contype='u'
               AND pg_get_constraintdef(constraint_row.oid) LIKE
                   '%(logical_payload_fingerprint)%'
               AND (schema_row.nspname,table_row.relname) IN (
                 ('strategy','v022_strategy_target_path'),
                 ('defense','v022_defense_decision_path'),
                 ('strategy','v022_merged_portfolio_target_path'),
                 ('experiment','v022_portfolio_cell_runtime_result')
               )
            """
        ).fetchone()
        assert logical_payload_unique_constraints == (0,)


@pytest.mark.integration
@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_m81_reserve_validator_uses_canonical_data_schema_after_reupgrade() -> None:
    psycopg_url = _reset()

    def validator_definition() -> str:
        with psycopg.connect(psycopg_url) as connection:
            value = connection.execute(
                """
                SELECT pg_get_functiondef(
                  'experiment.validate_v022_portfolio_evaluation_data_context()'::regprocedure
                )
                """
            ).fetchone()
        assert value is not None
        return str(value[0])

    upgraded = validator_definition()
    assert "FROM data.reserve_return reserve" in upgraded
    assert "FROM experiment.reserve_return reserve" not in upgraded

    assert DATABASE_URL is not None
    downgrade_database(DATABASE_URL, "20260812_80_v022_representative")
    downgraded = validator_definition()
    assert "FROM experiment.reserve_return reserve" in downgraded

    upgrade_database(DATABASE_URL)
    reupgraded = validator_definition()
    assert "FROM data.reserve_return reserve" in reupgraded


@pytest.mark.integration
@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_m82_evaluation_bundle_projection_survives_migration_roundtrip() -> None:
    psycopg_url = _reset()

    def bundle_columns() -> set[str]:
        with psycopg.connect(psycopg_url) as connection:
            return {
                str(row[0])
                for row in connection.execute(
                    """
                    SELECT column_name FROM information_schema.columns
                     WHERE table_schema='experiment'
                       AND table_name='v022_portfolio_evaluation_data_context'
                       AND column_name IN (
                         'data_bundle_version_id','data_bundle_artifact_id'
                       )
                    """
                ).fetchall()
            }

    assert bundle_columns() == {
        "data_bundle_version_id",
        "data_bundle_artifact_id",
    }
    assert DATABASE_URL is not None
    downgrade_database(DATABASE_URL, "20260812_81_v022_reserve_schema")
    assert bundle_columns() == set()
    upgrade_database(DATABASE_URL)
    assert bundle_columns() == {
        "data_bundle_version_id",
        "data_bundle_artifact_id",
    }


@pytest.mark.integration
@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
@pytest.mark.parametrize("failure_mode", ("empty", "bad_sum", "unverified"))
def test_typed_output_rejects_incomplete_or_unverified_manifest_closure(
    failure_mode: str,
) -> None:
    psycopg_url = _reset()
    with pytest.raises(
        psycopg.Error,
        match="incomplete or unverified object closure",
    ), psycopg.connect(psycopg_url) as connection:
        _publish_merge_output_fixture(
            connection,
            case_key=f"invalid-{failure_mode}",
            logical_payload_fingerprint=_fingerprint("shared-invalid-logical"),
            failure_mode=failure_mode,
        )


@pytest.mark.integration
@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_distinct_executions_may_publish_the_same_logical_payload() -> None:
    psycopg_url = _reset()
    logical_fingerprint = _fingerprint("same-logical-payload")
    for case_key in ("valid-a", "valid-b"):
        with psycopg.connect(psycopg_url) as connection:
            _publish_merge_output_fixture(
                connection,
                case_key=case_key,
                logical_payload_fingerprint=logical_fingerprint,
            )
    with psycopg.connect(psycopg_url) as connection:
        assert connection.execute(
            """
            SELECT count(*),count(DISTINCT work_execution_fingerprint)
              FROM strategy.v022_merged_portfolio_target_path
             WHERE logical_payload_fingerprint=%s
            """,
            (logical_fingerprint,),
        ).fetchone() == (2, 2)


@pytest.mark.integration
@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_m79_aggregation_run_and_cache_status_guards_are_monotonic() -> None:
    psycopg_url = _reset()
    run_id = uuid.uuid4()
    artifact_id = uuid.uuid4()
    aggregation_version_id = uuid.uuid4()
    execution_fingerprint = _fingerprint("m54-run")
    with psycopg.connect(psycopg_url) as connection:
        connection.execute("SET session_replication_role=replica")
        connection.execute(
            """
            INSERT INTO aggregation.aggregation_run (
              aggregation_run_id,artifact_id,aggregation_version_id,
              execution_fingerprint,resolved_parameters,executor_version,
              environment_fingerprint,status,started_at
            ) VALUES (%s,%s,%s,%s,'{}'::jsonb,'test',%s,'running',now())
            """,
            (
                run_id,
                    artifact_id,
                    aggregation_version_id,
                execution_fingerprint,
                _fingerprint("m54-environment"),
            ),
        )
        connection.execute(
            """
            INSERT INTO aggregation.aggregation_run_cache_entry (
              execution_fingerprint,aggregation_run_id,cache_state,
              eligibility_checked_at,invalidation_reason
            ) VALUES (%s,%s,'eligible',now(),NULL)
            """,
            (execution_fingerprint, run_id),
        )
        connection.execute("SET session_replication_role=origin")
        connection.execute("SAVEPOINT invalid_run_transition")
        with pytest.raises(psycopg.Error, match="status transition is invalid"):
            connection.execute(
                """
                UPDATE aggregation.aggregation_run
                   SET status='invalidated',invalidated_at=now()
                 WHERE aggregation_run_id=%s
                """,
                (run_id,),
            )
        connection.execute("ROLLBACK TO SAVEPOINT invalid_run_transition")
        connection.execute("SAVEPOINT invalid_cache_mutation")
        with pytest.raises(psycopg.Error, match="state transition is invalid"):
            connection.execute(
                """
                UPDATE aggregation.aggregation_run_cache_entry
                   SET invalidation_reason='{"tampered":true}'::jsonb
                 WHERE execution_fingerprint=%s
                """,
                (execution_fingerprint,),
            )
        connection.execute("ROLLBACK TO SAVEPOINT invalid_cache_mutation")
        connection.execute("SET session_replication_role=replica")


@pytest.mark.integration
@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_m79_downgrade_fails_closed_with_evaluation_context_identity() -> None:
    psycopg_url = _reset()
    context_id = uuid.uuid4()
    with psycopg.connect(psycopg_url) as connection:
        connection.execute("SET session_replication_role=replica")
        connection.execute(
            """
            INSERT INTO experiment.v022_portfolio_evaluation_data_context (
              portfolio_evaluation_data_context_id,artifact_id,
              evaluation_matrix_policy_id,evaluation_context_ordinal,
              benchmark_asset_id,benchmark_dataset_publication_id,
              benchmark_dataset_artifact_id,benchmark_calendar_version_id,
              benchmark_calendar_artifact_id,reserve_return_model_version_id,
              reserve_return_model_artifact_id,reserve_dataset_publication_id,
              reserve_dataset_artifact_id,coverage_start,coverage_end,pit_document,
              common_interval_document,context_fingerprint,
              artifact_semantic_fingerprint
            ) VALUES (
              %s,%s,%s,0,%s,%s,%s,%s,%s,%s,%s,%s,%s,
              DATE '2020-01-01',DATE '2020-12-31','{"pit":true}'::jsonb,
              '{"common":true}'::jsonb,%s,%s
            )
            """,
            (
                context_id,
                uuid.uuid4(),
                uuid.uuid4(),
                uuid.uuid4(),
                uuid.uuid4(),
                uuid.uuid4(),
                uuid.uuid4(),
                uuid.uuid4(),
                uuid.uuid4(),
                uuid.uuid4(),
                uuid.uuid4(),
                uuid.uuid4(),
                _fingerprint("downgrade-context"),
                _fingerprint("downgrade-context-artifact"),
            ),
        )
        connection.execute("SET session_replication_role=origin")
    assert DATABASE_URL is not None
    with pytest.raises(Exception, match="published runtime identities exist"):
        downgrade_database(DATABASE_URL, "20260812_78_v022_snapshot_ctx")
    assert database_status(DATABASE_URL).current_revision == (
        "20260821_142_asset_export"
    )


@pytest.mark.integration
@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_runtime_plan_rejects_non_exact_or_non_nested_date_ranges() -> None:
    psycopg_url = _reset()
    # Exercise the M79 range validator without the later M99 cohort trigger
    # intentionally taking precedence for unbound suites.
    assert DATABASE_URL is not None
    downgrade_database(DATABASE_URL, "20260816_98_v022_eval_cohort")
    with psycopg.connect(psycopg_url) as connection:
        connection.execute("SET session_replication_role=replica")
        plan_id = uuid.uuid4()
        plan_artifact_id = uuid.uuid4()
        connection.execute(
            """
            INSERT INTO lineage.artifact (
              artifact_id,artifact_type,artifact_key,version_number,status
            ) VALUES (%s,'v022_suite_runtime_plan',%s,1,'draft')
            """,
            (plan_artifact_id, f"v022_suite_runtime_plan__{_fingerprint('range-plan')}"),
        )
        connection.execute("SET session_replication_role=origin")
        statement = """
            INSERT INTO experiment.v022_suite_runtime_plan (
              suite_runtime_plan_id,artifact_id,research_suite_graph_run_binding_id,
              research_suite_id,compiled_research_graph_id,catalog_release_id,graph_run_id,
              compiled_execution_data_context_id,
              strategy_target_payload_contract_version_id,
              defense_decision_payload_contract_version_id,
              sleeve_merge_payload_contract_version_id,
              portfolio_cell_payload_contract_version_id,physical_encoding_version_id,
              contract_version,requested_range,effective_range,executor_version,
              environment_fingerprint,strategy_target_work_count,
              defense_decision_work_count,sleeve_merge_work_count,
              portfolio_cell_work_count,total_work_count,plan_fingerprint,
              artifact_semantic_fingerprint
            ) VALUES (
              %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'v0.22.0',
              %s::jsonb,%s::jsonb,'test',%s,1,0,1,1,3,%s,%s
            )
        """
        shared_ids = tuple(uuid.uuid4() for _ in range(11))
        for requested, effective in (
            (
                '{"start":"2020-01-01","end":"2020-12-31","extra":true}',
                '{"start":"2020-02-01","end":"2020-10-31"}',
            ),
            (
                '{"start":"2020-01-01","end":"2020-12-31"}',
                '{"start":"2019-12-31","end":"2020-10-31"}',
            ),
        ):
            connection.execute("SAVEPOINT invalid_range")
            with pytest.raises(psycopg.Error, match="exact ordered ISO date ranges"):
                connection.execute(
                    statement,
                    (
                        plan_id,
                        plan_artifact_id,
                        *shared_ids,
                        requested,
                        effective,
                        _fingerprint("range-env"),
                        _fingerprint("range-plan"),
                        _fingerprint("range-artifact"),
                    ),
                )
            connection.execute("ROLLBACK TO SAVEPOINT invalid_range")


@pytest.mark.integration
@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_evaluation_context_rejects_drifted_pit_document_projection() -> None:
    psycopg_url = _reset()
    # Isolate the original document-projection invariant. M82 adds a stricter
    # Data Bundle prerequisite which otherwise rejects this legacy fixture
    # before the document validator can be observed.
    assert DATABASE_URL is not None
    downgrade_database(DATABASE_URL, "20260812_81_v022_reserve_schema")
    context_fingerprint = _fingerprint("invalid-context-document")
    artifact_id = uuid.uuid4()
    with psycopg.connect(psycopg_url) as connection:
        connection.execute(
            """
            INSERT INTO lineage.artifact (
              artifact_id,artifact_type,artifact_key,version_number,status
            ) VALUES (%s,'v022_portfolio_evaluation_data_context',%s,1,'draft')
            """,
            (
                artifact_id,
                f"v022_portfolio_evaluation_data_context__{context_fingerprint}",
            ),
        )
        with pytest.raises(psycopg.Error, match="documents drift"):
            connection.execute(
                """
                INSERT INTO experiment.v022_portfolio_evaluation_data_context (
                  portfolio_evaluation_data_context_id,artifact_id,
                  evaluation_matrix_policy_id,evaluation_context_ordinal,
                  benchmark_asset_id,benchmark_dataset_publication_id,
                  benchmark_dataset_artifact_id,benchmark_calendar_version_id,
                  benchmark_calendar_artifact_id,reserve_return_model_version_id,
                  reserve_return_model_artifact_id,reserve_dataset_publication_id,
                  reserve_dataset_artifact_id,coverage_start,coverage_end,pit_document,
                  common_interval_document,context_fingerprint,
                  artifact_semantic_fingerprint
                ) VALUES (
                  %s,%s,%s,0,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                  DATE '2020-01-01',DATE '2020-12-31',
                  '{"policy_key":"wrong"}'::jsonb,
                  '{"policy_key":"wrong"}'::jsonb,%s,%s
                )
                """,
                (
                    uuid.uuid4(),
                    artifact_id,
                    *(uuid.uuid4() for _ in range(10)),
                    context_fingerprint,
                    _fingerprint("invalid-context-artifact"),
                ),
            )


@pytest.mark.integration
@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_research_cell_binding_rejects_wrong_policy_or_ordinal_context() -> None:
    psycopg_url = _reset()
    cell_id = uuid.uuid4()
    context_id = uuid.uuid4()
    context_artifact_id = uuid.uuid4()
    cell_policy_id = uuid.uuid4()
    context_policy_id = uuid.uuid4()
    with psycopg.connect(psycopg_url) as connection:
        connection.execute("SET session_replication_role=replica")
        connection.execute(
            """
            INSERT INTO lineage.artifact (
              artifact_id,artifact_type,artifact_key,version_number,status,
              semantic_fingerprint,content_hash,published_at
            ) VALUES (%s,'v022_portfolio_evaluation_data_context',%s,1,
                      'published',%s,%s,now())
            """,
            (
                context_artifact_id,
                f"context-{context_id}",
                _fingerprint("binding-context-artifact"),
                _fingerprint("binding-context-content"),
            ),
        )
        connection.execute(
            """
            INSERT INTO experiment.v022_research_cell (
              research_cell_id,research_suite_id,research_suite_branch_id,
              compiled_research_graph_id,compiled_strategy_branch_id,
              configuration_snapshot_id,evaluation_matrix_policy_id,
              evaluation_context_ordinal,ordinal,cell_key,
              evaluation_context_fingerprint,cell_fingerprint
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,0,0,%s,%s,%s)
            """,
            (
                cell_id,
                *(uuid.uuid4() for _ in range(5)),
                cell_policy_id,
                f"cell-{cell_id}",
                _fingerprint("cell-policy-context"),
                _fingerprint("cell"),
            ),
        )
        connection.execute(
            """
            INSERT INTO experiment.v022_portfolio_evaluation_data_context (
              portfolio_evaluation_data_context_id,artifact_id,
              evaluation_matrix_policy_id,evaluation_context_ordinal,
              benchmark_asset_id,benchmark_dataset_publication_id,
              benchmark_dataset_artifact_id,benchmark_calendar_version_id,
              benchmark_calendar_artifact_id,reserve_return_model_version_id,
              reserve_return_model_artifact_id,reserve_dataset_publication_id,
              reserve_dataset_artifact_id,coverage_start,coverage_end,pit_document,
              common_interval_document,context_fingerprint,
              artifact_semantic_fingerprint
            ) VALUES (
              %s,%s,%s,1,%s,%s,%s,%s,%s,%s,%s,%s,%s,
              DATE '2020-01-01',DATE '2020-12-31','{"pit":true}'::jsonb,
              '{"common":true}'::jsonb,%s,%s
            )
            """,
            (
                context_id,
                context_artifact_id,
                context_policy_id,
                *(uuid.uuid4() for _ in range(9)),
                _fingerprint("binding-data-context"),
                _fingerprint("binding-context-artifact"),
            ),
        )
        connection.execute("SET session_replication_role=origin")
        with pytest.raises(psycopg.Error, match="not exact and published"):
            connection.execute(
                """
                INSERT INTO experiment.v022_research_cell_evaluation_data_context_binding (
                  research_cell_id,portfolio_evaluation_data_context_id
                ) VALUES (%s,%s)
                """,
                (cell_id, context_id),
            )


@pytest.mark.integration
@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_portfolio_cell_work_spec_rejects_insufficient_evaluation_coverage() -> None:
    psycopg_url = _reset()
    # This fixture models the composed-context M79 contract. M141's simple
    # runtime guard is a separate invariant and would reject the fixture first.
    assert DATABASE_URL is not None
    downgrade_database(DATABASE_URL, "20260821_140_gate_import")
    ids = {key: uuid.uuid4() for key in (
        "plan", "plan_artifact", "suite", "graph", "run", "suite_binding",
        "risk_context", "catalog_release", "contract_strategy", "contract_defense",
        "contract_merge", "contract_cell", "encoding", "suite_branch",
        "compiled_branch", "snapshot", "cell", "policy", "context",
        "context_artifact", "work", "source_merge", "spec",
        "execution_context_artifact",
    )}
    execution_fingerprint = _fingerprint("coverage-work")
    plan_semantic_fingerprint = _fingerprint("coverage-plan-artifact")
    policy_context_fingerprint = _fingerprint("coverage-policy-context")
    data_context_fingerprint = _fingerprint("coverage-data-context")
    occurrence_key = "portfolio-cell:coverage"
    with psycopg.connect(psycopg_url) as connection:
        connection.execute("SET session_replication_role=replica")
        connection.execute(
            """
            INSERT INTO lineage.artifact (
              artifact_id,artifact_type,artifact_key,version_number,status
            ) VALUES (%s,'v022_suite_runtime_plan',%s,1,'draft')
            """,
            (ids["plan_artifact"], f"plan-{ids['plan']}"),
        )
        connection.execute(
            """
            INSERT INTO lineage.artifact (
              artifact_id,artifact_type,artifact_key,version_number,status,
              semantic_fingerprint,content_hash,published_at
            ) VALUES (%s,'v022_portfolio_evaluation_data_context',%s,1,
                      'published',%s,%s,now())
            """,
            (
                ids["context_artifact"],
                f"context-{ids['context']}",
                _fingerprint("coverage-context-artifact"),
                _fingerprint("coverage-context-content"),
            ),
        )
        connection.execute(
            """
            INSERT INTO experiment.v022_suite_runtime_plan (
              suite_runtime_plan_id,artifact_id,research_suite_graph_run_binding_id,
              research_suite_id,compiled_research_graph_id,catalog_release_id,graph_run_id,
              compiled_execution_data_context_id,
              strategy_target_payload_contract_version_id,
              defense_decision_payload_contract_version_id,
              sleeve_merge_payload_contract_version_id,
              portfolio_cell_payload_contract_version_id,physical_encoding_version_id,
              contract_version,requested_range,effective_range,executor_version,
              environment_fingerprint,strategy_target_work_count,
              defense_decision_work_count,sleeve_merge_work_count,
              portfolio_cell_work_count,total_work_count,plan_fingerprint,
              artifact_semantic_fingerprint
            ) VALUES (
              %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'v0.22.0',
              '{"start":"2020-01-01","end":"2020-12-31"}'::jsonb,
              '{"start":"2020-01-01","end":"2020-12-31"}'::jsonb,
              'test',%s,1,0,1,1,3,%s,%s
            )
            """,
            (
                ids["plan"], ids["plan_artifact"], ids["suite_binding"], ids["suite"],
                ids["graph"], ids["catalog_release"], ids["run"], ids["risk_context"],
                ids["contract_strategy"], ids["contract_defense"], ids["contract_merge"],
                ids["contract_cell"], ids["encoding"], _fingerprint("coverage-env"),
                _fingerprint("coverage-plan"), plan_semantic_fingerprint,
            ),
        )
        connection.execute(
            """
            INSERT INTO experiment.v022_research_suite_branch (
              research_suite_branch_id,research_suite_id,compiled_research_graph_id,
              compiled_strategy_branch_id,configuration_snapshot_id,ordinal,
              branch_key,branch_fingerprint,provenance_document
            ) VALUES (%s,%s,%s,%s,%s,0,'coverage-branch',%s,'{}'::jsonb)
            """,
            (
                ids["suite_branch"], ids["suite"], ids["graph"],
                ids["compiled_branch"], ids["snapshot"], _fingerprint("coverage-branch"),
            ),
        )
        connection.execute(
            """
            INSERT INTO experiment.v022_configuration_execution_context_binding (
              configuration_snapshot_id,compiled_research_graph_id,
              compiled_strategy_branch_id,compiled_execution_data_context_id,
              execution_data_context_artifact_id,execution_data_context_fingerprint,
              binding_document,binding_fingerprint
            ) VALUES (%s,%s,%s,%s,%s,%s,'{"fixture":true}'::jsonb,
                      strategy.v022_strategy_parameter_fingerprint(
                        '{"fixture":true}'::jsonb
                      ))
            """,
            (
                ids["snapshot"], ids["graph"], ids["compiled_branch"],
                ids["risk_context"], ids["execution_context_artifact"],
                _fingerprint("coverage-risk-context"),
            ),
        )
        connection.execute(
            """
            INSERT INTO experiment.v022_research_cell (
              research_cell_id,research_suite_id,research_suite_branch_id,
              compiled_research_graph_id,compiled_strategy_branch_id,
              configuration_snapshot_id,evaluation_matrix_policy_id,
              evaluation_context_ordinal,ordinal,cell_key,
              evaluation_context_fingerprint,cell_fingerprint
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,0,0,'coverage-cell',%s,%s)
            """,
            (
                ids["cell"], ids["suite"], ids["suite_branch"], ids["graph"],
                ids["compiled_branch"], ids["snapshot"], ids["policy"],
                policy_context_fingerprint, _fingerprint("coverage-cell"),
            ),
        )
        connection.execute(
            """
            INSERT INTO experiment.v022_portfolio_evaluation_data_context (
              portfolio_evaluation_data_context_id,artifact_id,
              evaluation_matrix_policy_id,evaluation_context_ordinal,
              benchmark_asset_id,benchmark_dataset_publication_id,
              benchmark_dataset_artifact_id,benchmark_calendar_version_id,
              benchmark_calendar_artifact_id,reserve_return_model_version_id,
              reserve_return_model_artifact_id,reserve_dataset_publication_id,
              reserve_dataset_artifact_id,coverage_start,coverage_end,pit_document,
              common_interval_document,context_fingerprint,
              artifact_semantic_fingerprint
            ) VALUES (
              %s,%s,%s,0,%s,%s,%s,%s,%s,%s,%s,%s,%s,
              DATE '2020-03-01',DATE '2020-08-31','{"pit":true}'::jsonb,
              '{"common":true}'::jsonb,%s,%s
            )
            """,
            (
                ids["context"], ids["context_artifact"], ids["policy"],
                *(uuid.uuid4() for _ in range(9)), data_context_fingerprint,
                _fingerprint("coverage-context-artifact"),
            ),
        )
        connection.execute(
            """
            INSERT INTO experiment.v022_research_cell_evaluation_data_context_binding (
              research_cell_id,portfolio_evaluation_data_context_id
            ) VALUES (%s,%s)
            """,
            (ids["cell"], ids["context"]),
        )
        connection.execute(
            """
            INSERT INTO workspace.v022_graph_work_item (
              graph_work_item_id,execution_fingerprint,work_kind,status,priority,
              lease_generation,fencing_token,attempt_count
            ) VALUES (%s,%s,'portfolio_cell','queued',100,0,0,0)
            """,
            (ids["work"], execution_fingerprint),
        )
        connection.execute(
            """
            INSERT INTO workspace.v022_graph_work_consumer (
              graph_run_id,graph_work_item_id,occurrence_kind,occurrence_key,
              binding_disposition
            ) VALUES (%s,%s,'portfolio_cell',%s,'execute')
            """,
            (ids["run"], ids["work"], occurrence_key),
        )
        connection.execute("SET session_replication_role=origin")
        document = {
            "work_execution_fingerprint": execution_fingerprint,
            "contract_version": "v0.22.0",
            "work_kind": "portfolio_cell",
            "occurrence_key": occurrence_key,
            "compiled_strategy_branch_id": str(ids["compiled_branch"]),
            "configuration_snapshot_id": str(ids["snapshot"]),
            "source_merge_work_item_id": str(ids["source_merge"]),
            "portfolio_evaluation_data_context_id": str(ids["context"]),
            "evaluation_policy_context_fingerprint": policy_context_fingerprint,
            "evaluation_data_context_fingerprint": data_context_fingerprint,
            "evaluation_context_ordinal": "0",
            "effective_range": {"start": "2020-01-01", "end": "2020-12-31"},
        }
        with pytest.raises(psycopg.Error, match="exact published Evaluation Data Context"):
            connection.execute(
                """
                INSERT INTO experiment.v022_portfolio_cell_work_spec (
                  portfolio_cell_work_spec_id,graph_work_item_id,work_kind,
                  suite_runtime_plan_id,research_suite_branch_id,research_cell_id,
                  compiled_strategy_branch_id,configuration_snapshot_id,
                  portfolio_evaluation_data_context_id,
                  output_payload_contract_version_id,physical_encoding_version_id,
                  source_merge_work_item_id,occurrence_key,specification_document,
                  specification_fingerprint,plan_artifact_semantic_fingerprint
                ) VALUES (%s,%s,'portfolio_cell',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                          %s::jsonb,%s,%s)
                """,
                (
                    ids["spec"], ids["work"], ids["plan"], ids["suite_branch"],
                    ids["cell"], ids["compiled_branch"], ids["snapshot"], ids["context"],
                    ids["contract_cell"], ids["encoding"], ids["source_merge"],
                    occurrence_key, json.dumps(document),
                    execution_fingerprint, plan_semantic_fingerprint,
                ),
            )


@pytest.mark.integration
@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_lineage_constraints_and_restrict_foreign_keys_are_enforced() -> None:
    psycopg_url = _reset()
    parent_id = uuid.uuid4()
    child_id = uuid.uuid4()
    dependency_id = uuid.uuid4()

    with psycopg.connect(psycopg_url) as connection:
        _insert_published(connection, parent_id, "parent", "a" * 64, "b" * 64)
        connection.execute(
            """
            INSERT INTO lineage.artifact (
                artifact_id, artifact_type, artifact_key, version_number, status
            ) VALUES (%s, 'test', 'child', 1, 'draft')
            """,
            (child_id,),
        )
        connection.execute(
            """
            INSERT INTO lineage.artifact_dependency (
                artifact_dependency_id, artifact_id, depends_on_artifact_id, role
            ) VALUES (%s, %s, %s, 'input')
            """,
            (dependency_id, child_id, parent_id),
        )
        connection.execute(
            """
            SELECT set_config('style_rotation.status_event_id', %s, true),
                   set_config('style_rotation.status_reason', 'test publication', true)
            """,
            (str(uuid.uuid4()),),
        )
        connection.execute(
            """
            UPDATE lineage.artifact SET status = 'published', semantic_fingerprint = %s,
                content_hash = %s, published_at = now() WHERE artifact_id = %s
            """,
            ("c" * 64, "d" * 64, child_id),
        )

        connection.execute("SAVEPOINT restrict_check")
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            connection.execute("DELETE FROM lineage.artifact WHERE artifact_id = %s", (parent_id,))
        connection.execute("ROLLBACK TO SAVEPOINT restrict_check")

        connection.execute("SAVEPOINT self_check")
        with pytest.raises(psycopg.DatabaseError):
            connection.execute(
                """
                INSERT INTO lineage.artifact_dependency (
                    artifact_dependency_id, artifact_id, depends_on_artifact_id, role
                ) VALUES (%s, %s, %s, 'invalid')
                """,
                (uuid.uuid4(), child_id, child_id),
            )
        connection.execute("ROLLBACK TO SAVEPOINT self_check")

        connection.execute("SAVEPOINT hash_check")
        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(
                """
                INSERT INTO lineage.artifact (
                    artifact_id, artifact_type, artifact_key, version_number, status,
                    semantic_fingerprint
                ) VALUES (%s, 'test', 'bad-hash', 1, 'draft', 'bad')
                """,
                (uuid.uuid4(),),
            )
        connection.execute("ROLLBACK TO SAVEPOINT hash_check")

        connection.rollback()


@pytest.mark.integration
@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_ops_engine_and_run_constraints_are_enforced() -> None:
    psycopg_url = _reset()
    artifact_id = uuid.uuid4()
    engine_definition_id = uuid.uuid4()
    engine_version_id = uuid.uuid4()
    run_attempt_id = uuid.uuid4()
    fingerprint = "f" * 64

    with psycopg.connect(psycopg_url) as connection:
        connection.execute(
            """
            INSERT INTO lineage.artifact (
                artifact_id, artifact_type, artifact_key, version_number, status
            ) VALUES (%s, 'test', 'test-engine-v1', 1, 'draft')
            """,
            (artifact_id,),
        )
        connection.execute(
            """
            INSERT INTO ops.engine_definition (
                engine_definition_id, engine_key, name, engine_type
            ) VALUES (%s, 'test-engine', 'Test Engine', 'test')
            """,
            (engine_definition_id,),
        )
        connection.execute(
            """
            INSERT INTO ops.engine_version (
                engine_version_id, engine_definition_id, artifact_id, version_number,
                semantic_version, git_commit, dependency_lock_hash, schema_revision,
                configuration_hash, numerical_environment
            ) VALUES (%s, %s, %s, 1, '0.2.0', 'test', %s,
                      '20260802_01_v02_foundation', %s, '{}'::jsonb)
            """,
            (engine_version_id, engine_definition_id, artifact_id, "3" * 64, "4" * 64),
        )
        connection.execute(
            """
            SELECT set_config('style_rotation.status_event_id', %s, true),
                   set_config('style_rotation.status_reason', 'test publication', true)
            """,
            (str(uuid.uuid4()),),
        )
        connection.execute(
            """
            UPDATE lineage.artifact
            SET status = 'published', semantic_fingerprint = %s, content_hash = %s,
                published_at = now()
            WHERE artifact_id = %s
            """,
            ("1" * 64, "2" * 64, artifact_id),
        )
        connection.execute(
            """
            INSERT INTO ops.run_attempt (
                run_attempt_id, engine_version_id, run_type, request_fingerprint,
                attempt_number, status
            ) VALUES (%s, %s, 'test', %s, 1, 'queued')
            """,
            (run_attempt_id, engine_version_id, fingerprint),
        )

        connection.execute("SAVEPOINT status_check")
        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(
                "UPDATE ops.run_attempt SET status = 'unknown' WHERE run_attempt_id = %s",
                (run_attempt_id,),
            )
        connection.execute("ROLLBACK TO SAVEPOINT status_check")

        connection.execute("SAVEPOINT duplicate_check")
        with pytest.raises(psycopg.errors.UniqueViolation):
            connection.execute(
                """
                INSERT INTO ops.run_attempt (
                    run_attempt_id, engine_version_id, run_type, request_fingerprint,
                    attempt_number, status
                ) VALUES (%s, %s, 'test', %s, 1, 'queued')
                """,
                (uuid.uuid4(), engine_version_id, fingerprint),
            )
        connection.execute("ROLLBACK TO SAVEPOINT duplicate_check")

        connection.execute("SAVEPOINT definition_check")
        with pytest.raises(psycopg.DatabaseError):
            connection.execute(
                "UPDATE ops.engine_definition SET name = 'changed' WHERE engine_definition_id = %s",
                (engine_definition_id,),
            )
        connection.execute("ROLLBACK TO SAVEPOINT definition_check")

        connection.rollback()


@pytest.mark.integration
@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_migration_can_downgrade_to_base_and_upgrade_again() -> None:
    _reset()
    assert DATABASE_URL is not None
    downgrade_database(DATABASE_URL, "base")
    downgraded = database_status(DATABASE_URL)
    assert downgraded.current_revision is None
    assert downgraded.missing_schemas == SCHEMA_NAMES

    upgrade_database(DATABASE_URL)
    upgraded = database_status(DATABASE_URL)
    assert upgraded.current_revision == "20260821_142_asset_export"
    assert upgraded.missing_schemas == ()


@pytest.mark.integration
@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_snapshot_context_migration_rejects_preexisting_shallow_composed_snapshot() -> None:
    psycopg_url = _reset()
    assert DATABASE_URL is not None
    downgrade_database(DATABASE_URL, "20260812_77_v022_defense_package")

    release_artifact_id = uuid.uuid4()
    component_artifact_id = uuid.uuid4()
    graph_artifact_id = uuid.uuid4()
    snapshot_artifact_id = uuid.uuid4()
    release_id = uuid.uuid4()
    graph_id = uuid.uuid4()
    snapshot_id = uuid.uuid4()
    branch_id = uuid.uuid4()
    with psycopg.connect(psycopg_url) as connection:
        connection.execute("SET session_replication_role=replica")
        for ordinal, (artifact_id, artifact_type, artifact_key) in enumerate(
            (
                (release_artifact_id, "v022_catalog_release", "precondition-release"),
                (
                    component_artifact_id,
                    "v022_defense_timing_version",
                    "precondition-timing",
                ),
                (graph_artifact_id, "v022_compiled_research_graph", "precondition-graph"),
                (
                    snapshot_artifact_id,
                    "v022_research_configuration_snapshot",
                    "precondition-snapshot",
                ),
            )
        ):
            connection.execute(
                """
                INSERT INTO lineage.artifact (
                  artifact_id,artifact_type,artifact_key,version_number,status,
                  semantic_fingerprint,content_hash,published_at
                ) VALUES (%s,%s,%s,1,'published',%s,%s,now())
                """,
                (
                    artifact_id,
                    artifact_type,
                    artifact_key,
                    format(ordinal + 1, "x") * 64,
                    format(ordinal + 5, "x") * 64,
                ),
            )
        connection.execute(
            """
            INSERT INTO workspace.v022_catalog_release (
              catalog_release_id,artifact_id,publisher_authorization_id,
              release_key,version_number,contract_version,processing_stage_count,
              release_fingerprint,source_manifest_hash,publisher_actor,published_at
            ) VALUES (%s,%s,%s,'precondition-release',1,'v0.22.0',3,%s,%s,'test',now())
            """,
            (release_id, release_artifact_id, uuid.uuid4(), "9" * 64, "a" * 64),
        )
        connection.execute(
            """
            INSERT INTO workspace.v022_catalog_release_component (
              catalog_release_id,component_artifact_id,component_kind,
              component_key,component_version,ordinal,component_fingerprint
            ) VALUES (
              %s,%s,'defense_timing_version','precondition-timing',1,0,%s
            )
            """,
            (release_id, component_artifact_id, "2" * 64),
        )
        connection.execute(
            """
            INSERT INTO workspace.compiled_research_graph (
              compiled_research_graph_id,artifact_id,graph_fingerprint,
              contract_version,compiler_version,catalog_release_id,
              asset_context_fingerprint,resolved_data_binding_fingerprint,
              frequency,normalized_graph,node_count,occurrence_count,edge_count,
              projection_count,aggregation_instance_count,strategy_branch_count
            ) VALUES (
              %s,%s,%s,'v0.22.0','precondition-test',%s,%s,%s,'weekly',
              '{}'::jsonb,0,1,0,0,1,1
            )
            """,
            (graph_id, graph_artifact_id, "b" * 64, release_id, "c" * 64, "d" * 64),
        )
        connection.execute(
            """
            INSERT INTO experiment.v022_research_configuration_snapshot (
              configuration_snapshot_id,artifact_id,compiled_research_graph_id,
              compiled_strategy_branch_id,configuration_fingerprint,
              semantic_identity_document,provenance_document,display_document
            ) VALUES (%s,%s,%s,%s,%s,'{}'::jsonb,'{}'::jsonb,'{}'::jsonb)
            """,
            (snapshot_id, snapshot_artifact_id, graph_id, branch_id, "e" * 64),
        )
        connection.execute("SET session_replication_role=origin")

    with pytest.raises(
        Exception,
        match="Cannot grandfather a composed Configuration Snapshot",
    ):
        upgrade_database(DATABASE_URL)
    assert database_status(DATABASE_URL).current_revision == (
        "20260812_77_v022_defense_package"
    )
    with psycopg.connect(psycopg_url) as connection:
        assert connection.execute(
            "SELECT to_regclass("
            "'experiment.v022_configuration_execution_context_binding')"
        ).fetchone() == (None,)
