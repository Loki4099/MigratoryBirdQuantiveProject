from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest

from style_rotation.lineage.service import ArtifactService, DependencyInput
from style_rotation.persistence.database import reset_database
from style_rotation.persistence.session import create_postgres_engine

DATABASE_URL = os.getenv("STYLE_ROTATION_TEST_DATABASE_URL")


def _service() -> ArtifactService:
    assert DATABASE_URL is not None
    reset_database(DATABASE_URL, "style_rotation_test", "test")
    return ArtifactService(create_postgres_engine(DATABASE_URL))


def _psycopg_url() -> str:
    assert DATABASE_URL is not None
    return DATABASE_URL.replace("postgresql+psycopg://", "postgresql://", 1)


@pytest.mark.integration
@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_publication_is_idempotent_frozen_and_manifested() -> None:
    service = _service()
    first = service.publish(
        artifact_type="test",
        artifact_key="stable",
        version_number=1,
        semantic_payload={"formula": "x + y"},
        content_payload={"rows": [1, 2, 3]},
    )
    second = service.publish(
        artifact_type="test",
        artifact_key="stable",
        version_number=1,
        semantic_payload={"formula": "x + y"},
        content_payload={"rows": [1, 2, 3]},
    )
    assert first.artifact_id == second.artifact_id
    assert first.manifest_hash == second.manifest_hash
    assert second.reused is True

    details = service.describe(first.artifact_id)
    assert details["artifact"]["status"] == "published"
    assert details["lineage_manifest"]["manifest_hash"] == first.manifest_hash

    with psycopg.connect(_psycopg_url()) as connection:
        with pytest.raises(psycopg.errors.RaiseException, match="immutable"):
            connection.execute(
                "UPDATE lineage.artifact SET content_hash = %s WHERE artifact_id = %s",
                ("f" * 64, first.artifact_id),
            )
        connection.rollback()


@pytest.mark.integration
@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_invalidation_taints_all_downstream_dependents() -> None:
    service = _service()
    parent = service.publish(
        artifact_type="test",
        artifact_key="parent",
        version_number=1,
        semantic_payload={"value": 1},
        content_payload={"value": 1},
    )
    child = service.publish(
        artifact_type="test",
        artifact_key="child",
        version_number=1,
        semantic_payload={"value": 2},
        content_payload={"value": 2},
        dependencies=(DependencyInput(parent.artifact_id, "input", 0),),
    )
    grandchild = service.publish(
        artifact_type="test",
        artifact_key="grandchild",
        version_number=1,
        semantic_payload={"value": 3},
        content_payload={"value": 3},
        dependencies=(DependencyInput(child.artifact_id, "input", 0),),
    )
    manifest = service.describe(grandchild.artifact_id)["lineage_manifest"]["manifest"]
    assert len(manifest["artifacts"]) == 3
    assert len(manifest["dependencies"]) == 2

    tainted = service.invalidate(parent.artifact_id, "upstream data defect")
    assert set(tainted) == {child.artifact_id, grandchild.artifact_id}
    statuses = {item["artifact_key"]: item["status"] for item in service.list_artifacts()}
    assert statuses == {"parent": "invalidated", "child": "tainted", "grandchild": "tainted"}


@pytest.mark.integration
@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_concurrent_equivalent_publications_converge_to_one_artifact() -> None:
    service = _service()

    def publish_once() -> tuple[str, bool]:
        result = service.publish(
            artifact_type="test",
            artifact_key="concurrent",
            version_number=1,
            semantic_payload={"same": True},
            content_payload={"same": True},
        )
        return str(result.artifact_id), result.reused

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _index: publish_once(), range(8)))
    assert len({artifact_id for artifact_id, _reused in results}) == 1
    assert sum(not reused for _artifact_id, reused in results) == 1
