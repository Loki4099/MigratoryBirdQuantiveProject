from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from sqlalchemy import text

from style_rotation.catalog.bootstrap import publish_catalogs
from style_rotation.catalog.versioning import semantic_version_number
from style_rotation.factor.service import publish_factor_catalog
from style_rotation.lineage.service import ArtifactService
from style_rotation.persistence.database import reset_database
from style_rotation.persistence.session import create_postgres_engine

pytestmark = pytest.mark.integration
DATABASE_URL = os.getenv("STYLE_ROTATION_TEST_DATABASE_URL")
PROJECT_ROOT = Path(__file__).parents[2]


@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_factor_catalog_materialization_is_typed_reusable_and_frozen(
    tmp_path: Path,
) -> None:
    assert DATABASE_URL is not None
    reset_database(DATABASE_URL, "style_rotation_test", "test")
    engine = create_postgres_engine(DATABASE_URL)
    publish_catalogs(ArtifactService(engine), PROJECT_ROOT / "v0.2" / "catalogs")
    catalog_path = PROJECT_ROOT / "v0.2" / "catalogs" / "factors.v0.2.0.json"

    first = publish_factor_catalog(engine, catalog_path)
    second = publish_factor_catalog(engine, catalog_path)
    assert first.definition_count == 12
    assert first.definition_version_count == 12
    assert first.variant_count == 28
    assert first.reused_count == 0
    assert second.reused_count == 53
    assert first.artifact_ids == second.artifact_ids
    assert first.release_artifact_id == second.release_artifact_id

    with engine.connect() as connection:
        counts = connection.execute(
            text(
                "SELECT (SELECT count(*) FROM factor.factor_definition), "
                "(SELECT count(*) FROM factor.factor_definition_version), "
                "(SELECT count(*) FROM factor.factor_variant), "
                "(SELECT count(*) FROM lineage.artifact_dependency dependency "
                "JOIN lineage.artifact artifact ON artifact.artifact_id = dependency.artifact_id "
                "WHERE artifact.artifact_type = 'factor_catalog_materialization')"
            )
        ).one()
        direction_columns = connection.execute(
            text(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_schema = 'factor' AND column_name LIKE '%direction%'"
            )
        ).scalar_one()
        sample = connection.execute(
            text(
                "SELECT definition.factor_key, version.implementation_key, variant.parameters, "
                "variant.required_price_observations FROM factor.factor_variant variant "
                "JOIN factor.factor_definition_version version ON "
                "version.factor_definition_version_id = variant.factor_definition_version_id "
                "JOIN factor.factor_definition definition ON "
                "definition.factor_definition_id = version.factor_definition_id "
                "WHERE variant.variant_key = 'total_return__w252'"
            )
        ).one()
    assert counts == (12, 12, 28, 53)
    assert direction_columns == 0
    assert sample == ("total_return", "total_return_v1", {"window": 252}, 253)

    changed = json.loads(catalog_path.read_text(encoding="utf-8"))
    changed["definitions"][0]["formula"] = "silently changed formula"
    changed_path = tmp_path / "factors.changed.json"
    changed_path.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError, match="does not match the published M0 artifact"):
        publish_factor_catalog(engine, changed_path)

    next_release = json.loads(catalog_path.read_text(encoding="utf-8"))
    next_release["catalog_version"] = "0.2.1"
    next_path = tmp_path / "factors.v0.2.1.json"
    next_path.write_text(json.dumps(next_release), encoding="utf-8")
    ArtifactService(engine).publish(
        artifact_type="research_catalog",
        artifact_key="factor_catalog",
        version_number=semantic_version_number("0.2.1"),
        semantic_payload=next_release,
        content_payload=next_release,
        reason="test unchanged definitions in a new catalog release",
    )
    next_materialization = publish_factor_catalog(engine, next_path)
    assert next_materialization.reused_count == 52
    assert next_materialization.artifact_ids == first.artifact_ids
    assert next_materialization.release_artifact_id != first.release_artifact_id

    with (
        pytest.raises(Exception, match="only change while their artifact is draft"),
        engine.begin() as connection,
    ):
        connection.execute(text("UPDATE factor.factor_variant SET required_price_observations = 1"))
