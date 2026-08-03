from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from sqlalchemy import text

from style_rotation.catalog.bootstrap import publish_catalogs
from style_rotation.factor.service import publish_factor_catalog
from style_rotation.lineage.service import ArtifactService
from style_rotation.persistence.database import reset_database
from style_rotation.persistence.session import create_postgres_engine
from style_rotation.signal.service import publish_signal_catalog

pytestmark = pytest.mark.integration
DATABASE_URL = os.getenv("STYLE_ROTATION_TEST_DATABASE_URL")
PROJECT_ROOT = Path(__file__).parents[2]


@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_signal_catalog_is_typed_reusable_and_traces_exact_factor_variants(
    tmp_path: Path,
) -> None:
    assert DATABASE_URL is not None
    reset_database(DATABASE_URL, "style_rotation_test", "test")
    engine = create_postgres_engine(DATABASE_URL)
    catalog_root = PROJECT_ROOT / "v0.2" / "catalogs"
    publish_catalogs(ArtifactService(engine), catalog_root)
    factor_publication = publish_factor_catalog(engine, catalog_root / "factors.v0.2.0.json")
    signal_path = catalog_root / "signals.v0.2.0.json"

    first = publish_signal_catalog(engine, signal_path)
    second = publish_signal_catalog(engine, signal_path)
    assert first.template_count == 27
    assert first.definition_count == 51
    assert first.version_count == 51
    assert first.product_eligible_count == 41
    assert first.reused_count == 0
    assert second.reused_count == 103
    assert first.artifact_ids == second.artifact_ids
    assert first.release_artifact_id == second.release_artifact_id

    with engine.connect() as connection:
        counts = connection.execute(
            text(
                "SELECT (SELECT count(*) FROM signal.signal_definition), "
                "(SELECT count(*) FROM signal.signal_version), "
                "(SELECT count(*) FROM signal.signal_definition WHERE product_eligible), "
                "(SELECT count(*) FROM lineage.artifact_dependency dependency "
                "JOIN lineage.artifact artifact ON artifact.artifact_id = dependency.artifact_id "
                "WHERE artifact.artifact_type = 'signal_catalog_materialization')"
            )
        ).one()
        continuous = connection.execute(
            text(
                "SELECT definition.template_key, variant.variant_key, version.direction, "
                "version.normalization, version.extreme_policy, version.missing_policy, "
                "version.tie_policy, version.output_type, version.rule, "
                "version.evaluation_horizon_policy FROM signal.signal_version version "
                "JOIN signal.signal_definition definition ON "
                "definition.signal_definition_id = version.signal_definition_id "
                "JOIN factor.factor_variant variant ON "
                "variant.factor_variant_id = version.factor_variant_id "
                "WHERE definition.signal_key = "
                "'return_continuation__total_return__w252'"
            )
        ).one()
        threshold = connection.execute(
            text(
                "SELECT version.normalization, version.tie_policy, version.rule "
                "FROM signal.signal_version version JOIN signal.signal_definition definition "
                "ON definition.signal_definition_id = version.signal_definition_id "
                "WHERE definition.signal_key = "
                "'price_above_ma_state__moving_average_ratio__s1_l200'"
            )
        ).one()
        version_dependency_count = connection.execute(
            text(
                "SELECT count(*) FROM lineage.artifact_dependency dependency "
                "JOIN lineage.artifact artifact ON artifact.artifact_id = dependency.artifact_id "
                "WHERE artifact.artifact_type = 'signal_version'"
            )
        ).scalar_one()
    assert counts == (51, 51, 41, 103)
    assert continuous == (
        "return_continuation",
        "total_return__w252",
        "higher_is_better",
        "cross_sectional_centered_rank_-1_1",
        "none",
        "error_after_common_warmup",
        "average_rank",
        "continuous",
        None,
        "explicit_evaluation_target_required",
    )
    assert threshold == (
        "none",
        "not_applicable",
        {"operator": ">", "threshold": 0, "true_score": 1, "false_score": -1},
    )
    assert version_dependency_count == 102

    changed = json.loads(signal_path.read_text(encoding="utf-8"))
    changed["templates"][0]["direction"] = "lower_is_better"
    changed_path = tmp_path / "signals.changed.json"
    changed_path.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError, match="does not match the published M0 artifact"):
        publish_signal_catalog(engine, changed_path)

    with (
        pytest.raises(Exception, match="only change while their artifact is draft"),
        engine.begin() as connection,
    ):
        connection.execute(
            text("UPDATE signal.signal_version SET normalization = 'silently_changed'")
        )

    assert len(factor_publication.artifact_ids) == 52
