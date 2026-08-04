from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from sqlalchemy import text

from style_rotation.catalog.bootstrap import publish_catalogs
from style_rotation.factor.service import publish_factor_catalog
from style_rotation.lineage.service import ArtifactService
from style_rotation.model.service import publish_model_catalog
from style_rotation.persistence.database import reset_database
from style_rotation.persistence.session import create_postgres_engine
from style_rotation.signal.service import publish_signal_catalog

pytestmark = pytest.mark.integration
DATABASE_URL = os.getenv("STYLE_ROTATION_TEST_DATABASE_URL")
PROJECT_ROOT = Path(__file__).parents[2]


@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_model_catalog_materializes_exact_signal_versions_and_freezes_structure(
    tmp_path: Path,
) -> None:
    assert DATABASE_URL is not None
    reset_database(DATABASE_URL, "style_rotation_test", "test")
    engine = create_postgres_engine(DATABASE_URL)
    catalog_root = PROJECT_ROOT / "v0.2" / "catalogs"
    publish_catalogs(ArtifactService(engine), catalog_root)
    publish_factor_catalog(engine, catalog_root / "factors.v0.2.0.json")
    publish_signal_catalog(engine, catalog_root / "signals.v0.2.0.json")

    model_path = catalog_root / "models.v0.2.0.json"
    first = publish_model_catalog(engine, model_path)
    second = publish_model_catalog(engine, model_path)
    assert first.method_count == 3
    assert first.definition_count == 1
    assert first.definition_version_count == 1
    assert first.specification_count == 86
    assert first.dimension_count == 151
    assert first.component_count == 331
    assert first.reused_count == 0
    assert second.reused_count == 95
    assert first.artifact_ids == second.artifact_ids
    assert first.release_artifact_id == second.release_artifact_id

    with engine.connect() as connection:
        counts = connection.execute(
            text(
                "SELECT (SELECT count(*) FROM model.model_method_definition), "
                "(SELECT count(*) FROM model.model_method_version), "
                "(SELECT count(*) FROM model.model_definition), "
                "(SELECT count(*) FROM model.model_definition_version), "
                "(SELECT count(*) FROM model.model_specification), "
                "(SELECT count(*) FROM model.model_dimension), "
                "(SELECT count(*) FROM model.model_component)"
            )
        ).one()
        trend_weights = connection.execute(
            text(
                "SELECT dimension.dimension_key, dimension.weight "
                "FROM model.model_dimension dimension "
                "JOIN model.model_specification specification ON "
                "specification.model_specification_id = dimension.model_specification_id "
                "WHERE specification.specification_key = 'trend_tilt_v1' "
                "ORDER BY dimension.ordinal"
            )
        ).all()
        vote = connection.execute(
            text(
                "SELECT specification.tie_output, specification.output_type, method.method_key, "
                "array_agg(DISTINCT dimension.input_transform) "
                "FROM model.model_specification specification "
                "JOIN model.model_method_version method_version ON "
                "method_version.model_method_version_id = "
                "specification.overall_method_version_id "
                "JOIN model.model_method_definition method ON "
                "method.model_method_definition_id = method_version.model_method_definition_id "
                "JOIN model.model_dimension dimension ON dimension.model_specification_id = "
                "specification.model_specification_id WHERE specification.specification_key = "
                "'five_dimension_majority_vote_v1' GROUP BY specification.tie_output, "
                "specification.output_type, method.method_key"
            )
        ).one()
        single_signal = connection.execute(
            text(
                "SELECT definition.signal_key, component.weight "
                "FROM model.model_component component "
                "JOIN model.model_specification specification ON "
                "specification.model_specification_id = "
                "component.model_specification_id JOIN signal.signal_version version ON "
                "version.signal_version_id = component.signal_version_id "
                "JOIN signal.signal_definition definition ON "
                "definition.signal_definition_id = version.signal_definition_id "
                "WHERE "
                "specification.specification_key = "
                "'single_signal__return_continuation__total_return__w252'"
            )
        ).one()
    assert counts == (3, 3, 1, 1, 86, 151, 331)
    assert [item[0] for item in trend_weights] == [
        "momentum_trend",
        "reversal",
        "tail_distribution",
        "volatility_risk",
        "volume_liquidity",
    ]
    assert [float(item[1]) for item in trend_weights] == pytest.approx([0.4, 0.1, 0.1, 0.2, 0.2])
    assert vote == ("neutral", "directional_score", "majority_vote", ["sign"])
    assert single_signal[0] == "return_continuation__total_return__w252"
    assert float(single_signal[1]) == 1.0

    changed = json.loads(model_path.read_text(encoding="utf-8"))
    changed["fixed_weight_specifications"][0]["dimension_weights"]["momentum_trend"] = 0.35
    changed["fixed_weight_specifications"][0]["dimension_weights"]["reversal"] = 0.15
    changed_path = tmp_path / "models.changed.json"
    changed_path.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError, match="does not match the published M0 artifact"):
        publish_model_catalog(engine, changed_path)

    with (
        pytest.raises(Exception, match="children can only change while artifact is draft"),
        engine.begin() as connection,
    ):
        connection.execute(text("UPDATE model.model_component SET weight = 0.5"))
