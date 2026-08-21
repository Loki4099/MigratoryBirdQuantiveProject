from __future__ import annotations

import os
import uuid
from datetime import date, timedelta
from pathlib import Path

import pytest
from sqlalchemy import text

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.persistence.database import reset_database
from style_rotation.persistence.session import create_postgres_engine
from style_rotation.v022.incremental_runtime import (
    IncrementalExecutionContract,
    OutputPartition,
    plan_incremental_run,
    record_partition_plan,
)
from style_rotation.v022.publication import (
    CatalogPublicationContext,
    publish_catalog_release,
)

pytestmark = pytest.mark.integration
DATABASE_URL = os.getenv("STYLE_ROTATION_TEST_DATABASE_URL")
ROOT = Path(__file__).parents[2]
CATALOG = ROOT / "v0.22/catalogs/releases/catalog_release.v0.22.2.json"
CONTEXT = CatalogPublicationContext(
    actor_key="local_researcher",
    reviewer_actor="local_researcher",
    trusted_local_authorization_bootstrap=True,
)


@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_partition_plan_is_idempotent_and_cannot_override_published_contract() -> None:
    assert DATABASE_URL is not None
    reset_database(DATABASE_URL, "style_rotation_test", "test")
    engine = create_postgres_engine(DATABASE_URL)
    publish_catalog_release(engine, CATALOG, context=CONTEXT)

    with engine.begin() as connection:
        node = (
            connection.execute(
                text(
                    """
                SELECT node_version_id,execution_contract
                FROM processing.node_version
                WHERE execution_contract->>'execution_mode'='full_recompute'
                ORDER BY node_version_id
                LIMIT 1
                """
                )
            )
            .mappings()
            .one()
        )
        document = node["execution_contract"]
        contract = IncrementalExecutionContract(
            execution_mode="full_recompute",
            partition_key=tuple(document["partition_key"]),
            lookback=document["lookback"],
            lookforward=document["lookforward"],
            revision_impact_policy=document["revision_impact_policy"],
        )
        node_run_id = uuid.uuid4()
        artifact_id = uuid.uuid4()
        connection.execute(
            text(
                """
                INSERT INTO lineage.artifact (
                  artifact_id,artifact_type,artifact_key,version_number,status
                ) VALUES (:artifact_id,'v022_node_run',:artifact_key,1,'draft')
                """
            ),
            {"artifact_id": artifact_id, "artifact_key": f"test-{node_run_id}"},
        )
        connection.execute(
            text(
                """
                INSERT INTO processing.node_run (
                  node_run_id,artifact_id,node_version_id,execution_fingerprint,
                  resolved_parameters,requested_range,executor_version,
                  environment_fingerprint,status,cache_eligible,started_at
                ) VALUES (
                  :node_run_id,:artifact_id,:node_version_id,:execution_fingerprint,
                  '{}'::jsonb,'{}'::jsonb,'integration-test',
                  :environment_fingerprint,'running',true,now()
                )
                """
            ),
            {
                "node_run_id": node_run_id,
                "artifact_id": artifact_id,
                "node_version_id": node["node_version_id"],
                "execution_fingerprint": sha256_hexdigest(("run", node_run_id)),
                "environment_fingerprint": sha256_hexdigest("integration-test"),
            },
        )

    sessions = tuple(date(2026, 1, 5) + timedelta(days=index) for index in range(6))
    partition_field = contract.partition_key[0]
    partitions = (
        OutputPartition({partition_field: "test-scope"}, sessions[:3]),
        OutputPartition({partition_field: "test-scope"}, sessions[3:]),
    )
    revisions = {session: sha256_hexdigest(("source", session)) for session in sessions}
    plan = plan_incremental_run(
        contract=contract,
        partitions=partitions,
        source_revisions=revisions,
    )

    first = record_partition_plan(engine, node_run_id=node_run_id, plan=plan)
    second = record_partition_plan(engine, node_run_id=node_run_id, plan=plan)

    assert first.partition_count == 2
    assert first.reused_existing_plan is False
    assert second.reused_existing_plan is True
    with engine.connect() as connection:
        rows = (
            connection.execute(
                text(
                    """
                SELECT status,partition_document
                FROM processing.node_run_partition
                WHERE node_run_id=:node_run_id
                ORDER BY partition_document->'output_range'->>'start'
                """
                ),
                {"node_run_id": node_run_id},
            )
            .mappings()
            .all()
        )
    assert [row["status"] for row in rows] == ["planned", "planned"]
    assert rows[1]["partition_document"]["calculation_range"]["start"] <= (sessions[3].isoformat())

    illegal_plan = plan_incremental_run(
        contract=IncrementalExecutionContract(
            execution_mode="windowed",
            partition_key=contract.partition_key,
            lookback=contract.lookback,
            lookforward=contract.lookforward,
            revision_impact_policy=contract.revision_impact_policy,
        ),
        partitions=partitions,
        source_revisions=revisions,
    )
    with pytest.raises(ValueError, match="does not match"):
        record_partition_plan(engine, node_run_id=node_run_id, plan=illegal_plan)
    engine.dispose()
