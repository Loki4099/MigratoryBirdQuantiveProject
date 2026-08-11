from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from style_rotation.catalog.bootstrap import publish_catalogs
from style_rotation.factor.service import publish_factor_catalog
from style_rotation.lineage.service import ArtifactService
from style_rotation.persistence.database import reset_database
from style_rotation.persistence.session import create_postgres_engine
from style_rotation.signal.service import publish_signal_catalog
from style_rotation.strategy.service import publish_strategy_catalog

pytestmark = pytest.mark.integration
DATABASE_URL = os.getenv("STYLE_ROTATION_TEST_DATABASE_URL")
PROJECT_ROOT = Path(__file__).parents[2]


@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_strategy_catalog_materializes_exact_semantics_and_is_immutable(tmp_path: Path) -> None:
    assert DATABASE_URL is not None
    reset_database(DATABASE_URL, "style_rotation_test", "test")
    engine = create_postgres_engine(DATABASE_URL)
    catalog_root = PROJECT_ROOT / "v0.2" / "catalogs"
    publish_catalogs(ArtifactService(engine), catalog_root)
    publish_factor_catalog(engine, catalog_root / "factors.v0.2.0.json")
    publish_signal_catalog(engine, catalog_root / "signals.v0.2.0.json")

    catalog_path = catalog_root / "strategies.v0.2.0.json"
    first = publish_strategy_catalog(engine, catalog_path)
    second = publish_strategy_catalog(engine, catalog_path)
    assert first.definition_count == 1
    assert first.definition_version_count == 1
    assert first.input_contract_count == 1
    assert first.variant_count == 9
    assert first.schedule_count == 2
    assert first.execution_policy_count == 1
    assert first.reused_count == 0
    assert second.reused_count == 19
    assert first.artifact_ids == second.artifact_ids
    assert first.release_artifact_id == second.release_artifact_id

    with engine.connect() as connection:
        counts = connection.execute(
            text(
                "SELECT (SELECT count(*) FROM ops.rebalance_schedule_definition), "
                "(SELECT count(*) FROM ops.rebalance_schedule_version), "
                "(SELECT count(*) FROM ops.execution_policy_definition), "
                "(SELECT count(*) FROM ops.execution_policy_version), "
                "(SELECT count(*) FROM strategy.strategy_definition), "
                "(SELECT count(*) FROM strategy.strategy_definition_version), "
                "(SELECT count(*) FROM strategy.strategy_input_contract), "
                "(SELECT count(*) FROM strategy.strategy_variant), "
                "(SELECT count(*) FROM strategy.strategy_product_definition), "
                "(SELECT count(*) FROM strategy.strategy_product_version)"
            )
        ).one()
        variants = connection.execute(
            text(
                "SELECT variant.template_key, variant.target_k, variant.research_tier, "
                "variant.selection_order, variant.auxiliary_signal_version_id IS NOT NULL "
                "FROM strategy.strategy_variant variant ORDER BY variant.template_key, "
                "variant.target_k"
            )
        ).all()
        trend_signal_keys = (
            connection.execute(
                text(
                    "SELECT DISTINCT definition.signal_key FROM strategy.strategy_variant variant "
                    "JOIN signal.signal_version version ON version.signal_version_id = "
                    "variant.auxiliary_signal_version_id "
                    "JOIN signal.signal_definition definition ON "
                    "definition.signal_definition_id = version.signal_definition_id"
                )
            )
            .scalars()
            .all()
        )
        release_payload = connection.execute(
            text(
                "SELECT artifact.content_hash FROM lineage.artifact artifact "
                "WHERE artifact.artifact_id = :artifact"
            ),
            {"artifact": first.release_artifact_id},
        ).scalar_one()
    assert counts == (2, 2, 1, 1, 1, 1, 1, 9, 0, 0)
    assert {item[1] for item in variants} == {1, 2, 3}
    assert sum(item[2] == "canonical" for item in variants) == 3
    assert sum(bool(item[4]) for item in variants) == 6
    assert trend_signal_keys == ["price_above_ma_state__moving_average_ratio__s1_l200"]
    assert len(release_payload) == 64

    changed = json.loads(catalog_path.read_text(encoding="utf-8"))
    changed["definition"]["hypothesis"] = "A different but valid hypothesis."
    changed_path = tmp_path / "strategies.changed.json"
    changed_path.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError, match="does not match the published M0 artifact"):
        publish_strategy_catalog(engine, changed_path)

    with (
        pytest.raises(ProgrammingError, match="only change while their artifact is draft"),
        engine.begin() as connection,
    ):
        connection.execute(text("UPDATE strategy.strategy_variant SET target_k = 1"))
