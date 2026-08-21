from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, Engine, text

from style_rotation.core.canonical import sha256_hexdigest

BASELINE_SCHEMA_VERSION = "1.0.0"
EXPECTED_INVENTORY = {
    "factor_family_count": 12,
    "factor_variant_count": 28,
    "signal_family_count": 27,
    "signal_version_count": 51,
    "model_specification_count": 86,
    "factor_dataset_count": 56,
    "signal_dataset_count": 102,
    "model_dataset_count": 172,
}


class V021BaselineError(RuntimeError):
    """Raised when the v0.21 evidence cannot be frozen without ambiguity."""


def build_v021_baseline(
    engine: Engine,
    repo_root: Path,
    *,
    source_commit: str,
    contract_tag: str,
    frozen_at: str,
) -> dict[str, Any]:
    root = repo_root.resolve()
    source = _resolve_commit(root, source_commit)
    contract_commit = _resolve_commit(root, contract_tag)
    source_files = _source_files(root, source)

    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            database = _database_snapshot(connection)
            inventory = _inventory(connection)
            outputs = {
                "factor_datasets": _rows(connection, FACTOR_DATASET_SQL),
                "signal_datasets": _rows(connection, SIGNAL_DATASET_SQL),
                "model_datasets": _rows(connection, MODEL_DATASET_SQL),
            }
            research = {
                "engine_versions": _rows(connection, ENGINE_VERSION_SQL),
                "compiled_model_instances": _rows(connection, COMPILED_MODEL_SQL),
                "compiled_strategy_versions": _rows(connection, COMPILED_STRATEGY_SQL),
                "frozen_cell_results": _rows(connection, CELL_RESULT_SQL),
                "active_products": _rows(connection, ACTIVE_PRODUCT_SQL),
                "active_product_artifact_closure": _rows(connection, PRODUCT_CLOSURE_SQL),
                "active_product_dependency_edges": _rows(connection, PRODUCT_EDGE_SQL),
            }
            checks = _checks(connection, inventory, outputs, research)
            transaction.commit()
        except Exception:
            transaction.rollback()
            raise

    payload: dict[str, Any] = {
        "baseline_schema_version": BASELINE_SCHEMA_VERSION,
        "baseline_id": "v021-oracle-v0.22.0-m0",
        "status": "complete" if all(checks.values()) else "blocked",
        "frozen_at": frozen_at,
        "source": {
            "v021_source_commit": source,
            "v022_contract_tag": contract_tag,
            "v022_contract_commit": contract_commit,
            "dependency_lock_sha256": _normalized_text_sha256(root / "requirements.lock"),
            "pyproject_sha256": _normalized_text_sha256(root / "pyproject.toml"),
            "source_files": source_files,
        },
        "database_snapshot": database,
        "inventory": inventory,
        "oracle_outputs": outputs,
        "research_and_product_evidence": research,
        "checks": checks,
        "comparison_policy": {
            "expected_values_source": "frozen_artifact_content_hashes_only",
            "runtime_recalculation_may_generate_expected_values": False,
            "legacy_missing_reason": "legacy_unknown",
            "float_abs_rel_tolerance": "1e-12",
            "decimal_quantum": "0.000000000000000001",
            "unexplained_mismatch_allowed": False,
        },
    }
    payload["payload_sha256"] = sha256_hexdigest(payload)
    if payload["status"] != "complete":
        failed = sorted(key for key, value in checks.items() if not value)
        raise V021BaselineError(f"v0.21 baseline checks failed: {', '.join(failed)}")
    return payload


def write_baseline(path: Path, payload: Mapping[str, Any]) -> None:
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite frozen baseline: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_pretty_json(payload), encoding="utf-8", newline="\n")


def verify_baseline(path: Path, expected: Mapping[str, Any]) -> None:
    actual = json.loads(path.read_text(encoding="utf-8"))
    if actual != expected:
        raise V021BaselineError(
            "Frozen v0.21 baseline differs from the current repeatable snapshot"
        )


def _database_snapshot(connection: Connection) -> dict[str, Any]:
    row = connection.execute(
        text(
            "SELECT current_database() database_name, current_setting('server_version') "
            "server_version, (SELECT version_num FROM alembic_version) alembic_revision"
        )
    ).mappings().one()
    return {str(key): _normalize(value) for key, value in row.items()}


def _inventory(connection: Connection) -> dict[str, Any]:
    row = connection.execute(text(INVENTORY_SQL)).mappings().one()
    distribution = _rows(connection, MODEL_DISTRIBUTION_SQL)
    return {**_normalize(dict(row)), "model_specification_distribution": distribution}


def _checks(
    connection: Connection,
    inventory: Mapping[str, Any],
    outputs: Mapping[str, Sequence[Mapping[str, Any]]],
    research: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, bool]:
    checks = {
        key: inventory.get(key) == value for key, value in EXPECTED_INVENTORY.items()
    }
    referenced_artifacts = {
        str(row["artifact_id"])
        for collection in (*outputs.values(), *research.values())
        for row in collection
        if row.get("artifact_id")
    }
    checks["all_referenced_artifacts_are_published"] = all(
        row.get("artifact_status", "published") == "published"
        for collection in (*outputs.values(), *research.values())
        for row in collection
    )
    invalidated = 0
    if referenced_artifacts:
        invalidated = connection.execute(
            text(
                "SELECT count(*) FROM lineage.artifact_invalidation "
                "WHERE artifact_id = ANY(:artifact_ids)"
            ),
            {"artifact_ids": sorted(referenced_artifacts)},
        ).scalar_one()
    checks["no_referenced_artifact_is_invalidated"] = invalidated == 0
    active_products = research["active_products"]
    checks["all_active_products_have_artifact_closure"] = not active_products or bool(
        research["active_product_artifact_closure"]
    )
    checks["workspace_signal_equal_oracle_is_frozen"] = any(
        row.get("preset_key") == "linear_weighted__signal_equal_v1"
        for row in active_products
    )
    checks["frozen_cell_results_exist"] = bool(research["frozen_cell_results"])
    return checks


def _rows(connection: Connection, sql: str) -> list[dict[str, Any]]:
    return [_normalize(dict(row)) for row in connection.execute(text(sql)).mappings()]


def _source_files(root: Path, source_commit: str) -> list[dict[str, Any]]:
    frozen_paths = _git_text(
        root,
        "ls-tree",
        "-r",
        "--name-only",
        source_commit,
        "--",
        "migrations/versions",
        "v0.2/catalogs",
        "v0.21/catalogs",
    ).splitlines()
    relative_paths = ["pyproject.toml", "requirements.lock"]
    relative_paths.extend(
        path
        for path in frozen_paths
        if path.endswith(".py") or path.endswith(".json")
    )
    output: list[dict[str, Any]] = []
    for relative in relative_paths:
        path = root / relative
        working_bytes = _normalize_text_bytes(path.read_bytes())
        working_hash = hashlib.sha256(working_bytes).hexdigest()
        source_bytes = _normalize_text_bytes(_git_bytes(root, source_commit, relative))
        source_hash = hashlib.sha256(source_bytes).hexdigest()
        if working_hash != source_hash:
            raise V021BaselineError(f"Working file differs from v0.21 source commit: {relative}")
        output.append(
            {
                "path": relative,
                "canonical_lf_bytes": len(working_bytes),
                "sha256": working_hash,
                "matches_v021_source_commit": True,
            }
        )
    return output


def _resolve_commit(root: Path, revision: str) -> str:
    return _git_text(root, "rev-parse", f"{revision}^{{commit}}")


def _git_bytes(root: Path, commit: str, path: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(root), "show", f"{commit}:{path}"],
        check=True,
        capture_output=True,
    )
    return result.stdout


def _git_text(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def _normalized_text_sha256(path: Path) -> str:
    return hashlib.sha256(_normalize_text_bytes(path.read_bytes())).hexdigest()


def _normalize_text_bytes(value: bytes) -> bytes:
    return value.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _normalize(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _normalize(item) for key, item in sorted(value.items())}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_normalize(item) for item in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, (UUID, Decimal)):
        return str(value)
    return value


def _pretty_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True) + "\n"


INVENTORY_SQL = """
SELECT
  (SELECT count(*) FROM factor.factor_definition) factor_family_count,
  (SELECT count(*) FROM factor.factor_variant) factor_variant_count,
  (SELECT count(DISTINCT template_key) FROM signal.signal_definition) signal_family_count,
  (SELECT count(*) FROM signal.signal_version) signal_version_count,
  (SELECT count(*) FROM model.model_specification) model_specification_count,
  (SELECT count(*) FROM factor.factor_dataset) factor_dataset_count,
  (SELECT count(*) FROM signal.signal_dataset) signal_dataset_count,
  (SELECT count(*) FROM model.model_dataset) model_dataset_count,
  (SELECT count(*) FROM product.product_enrollment WHERE lifecycle = 'active') active_product_count
"""

MODEL_DISTRIBUTION_SQL = """
SELECT specification_type, count(*) specification_count
FROM model.model_specification
GROUP BY specification_type
ORDER BY specification_type
"""

FACTOR_DATASET_SQL = """
SELECT a.artifact_id::text artifact_id, a.status artifact_status,
       a.semantic_fingerprint, a.content_hash, fd.factor_key, fv.variant_key,
       bd.bundle_key, bv.version_number bundle_version, ud.universe_key,
       uv.version_number universe_version, ed.engine_key, ev.version_number engine_version,
       ds.coverage_start, ds.coverage_end, ds.row_count
FROM factor.factor_dataset ds
JOIN lineage.artifact a ON a.artifact_id = ds.artifact_id
JOIN factor.factor_variant fv ON fv.factor_variant_id = ds.factor_variant_id
JOIN factor.factor_definition_version fdv
  ON fdv.factor_definition_version_id = fv.factor_definition_version_id
JOIN factor.factor_definition fd ON fd.factor_definition_id = fdv.factor_definition_id
JOIN data.data_bundle_version bv ON bv.data_bundle_version_id = ds.data_bundle_version_id
JOIN data.data_bundle_definition bd ON bd.data_bundle_definition_id = bv.data_bundle_definition_id
JOIN catalog.universe_version uv ON uv.universe_version_id = ds.universe_version_id
JOIN catalog.universe_definition ud ON ud.universe_definition_id = uv.universe_definition_id
JOIN ops.engine_version ev ON ev.engine_version_id = ds.engine_version_id
JOIN ops.engine_definition ed ON ed.engine_definition_id = ev.engine_definition_id
ORDER BY fv.variant_key, bv.version_number, ds.coverage_start, a.artifact_id
"""

SIGNAL_DATASET_SQL = """
SELECT a.artifact_id::text artifact_id, a.status artifact_status,
       a.semantic_fingerprint, a.content_hash, sd.signal_key, sd.template_key,
       sv.version_number signal_version, sv.output_type, bd.bundle_key,
       bv.version_number bundle_version, ud.universe_key,
       uv.version_number universe_version, ed.engine_key, ev.version_number engine_version,
       ds.coverage_start, ds.coverage_end, ds.row_count
FROM signal.signal_dataset ds
JOIN lineage.artifact a ON a.artifact_id = ds.artifact_id
JOIN signal.signal_version sv ON sv.signal_version_id = ds.signal_version_id
JOIN signal.signal_definition sd ON sd.signal_definition_id = sv.signal_definition_id
JOIN data.data_bundle_version bv ON bv.data_bundle_version_id = ds.data_bundle_version_id
JOIN data.data_bundle_definition bd ON bd.data_bundle_definition_id = bv.data_bundle_definition_id
JOIN catalog.universe_version uv ON uv.universe_version_id = ds.universe_version_id
JOIN catalog.universe_definition ud ON ud.universe_definition_id = uv.universe_definition_id
JOIN ops.engine_version ev ON ev.engine_version_id = ds.engine_version_id
JOIN ops.engine_definition ed ON ed.engine_definition_id = ev.engine_definition_id
ORDER BY sd.signal_key, bv.version_number, ds.coverage_start, a.artifact_id
"""

MODEL_DATASET_SQL = """
SELECT a.artifact_id::text artifact_id, a.status artifact_status,
       a.semantic_fingerprint, a.content_hash, ms.specification_key,
       ms.specification_type, ms.active_dimension_count, ms.component_count,
       bd.bundle_key, bv.version_number bundle_version, ud.universe_key,
       uv.version_number universe_version, ed.engine_key, ev.version_number engine_version,
       ds.coverage_start, ds.coverage_end, ds.row_count, ds.input_set_hash
FROM model.model_dataset ds
JOIN lineage.artifact a ON a.artifact_id = ds.artifact_id
JOIN model.model_specification ms ON ms.model_specification_id = ds.model_specification_id
JOIN data.data_bundle_version bv ON bv.data_bundle_version_id = ds.data_bundle_version_id
JOIN data.data_bundle_definition bd ON bd.data_bundle_definition_id = bv.data_bundle_definition_id
JOIN catalog.universe_version uv ON uv.universe_version_id = ds.universe_version_id
JOIN catalog.universe_definition ud ON ud.universe_definition_id = uv.universe_definition_id
JOIN ops.engine_version ev ON ev.engine_version_id = ds.engine_version_id
JOIN ops.engine_definition ed ON ed.engine_definition_id = ev.engine_definition_id
ORDER BY ms.specification_key, bv.version_number, ds.coverage_start, a.artifact_id
"""

ENGINE_VERSION_SQL = """
SELECT a.artifact_id::text artifact_id, a.status artifact_status,
       a.semantic_fingerprint, a.content_hash, ed.engine_key, ev.version_number,
       ev.semantic_version, ev.git_commit, ev.dependency_lock_hash,
       ev.schema_revision, ev.configuration_hash, ev.numerical_environment
FROM ops.engine_version ev
JOIN ops.engine_definition ed ON ed.engine_definition_id = ev.engine_definition_id
JOIN lineage.artifact a ON a.artifact_id = ev.artifact_id
ORDER BY ed.engine_key, ev.version_number
"""

COMPILED_MODEL_SQL = """
SELECT crs.artifact_id::text artifact_id, a.status artifact_status,
       a.semantic_fingerprint, a.content_hash, cmi.compiled_model_instance_id::text,
       cmi.instance_key, cmi.preset_key, cmi.family_key, cmi.output_type,
       cmi.frequency, cmi.slot_assignments, cmi.instance_fingerprint,
       cmi.parameters, cmi.target_key
FROM workspace.compiled_model_instance cmi
JOIN workspace.compiled_research_spec crs
  ON crs.compiled_research_spec_id = cmi.compiled_research_spec_id
JOIN lineage.artifact a ON a.artifact_id = crs.artifact_id
ORDER BY cmi.created_at, cmi.compiled_model_instance_id
"""

COMPILED_STRATEGY_SQL = """
SELECT a.artifact_id::text artifact_id, a.status artifact_status,
       a.semantic_fingerprint, a.content_hash, csv.compiled_strategy_version_id::text,
       csv.branch_key, csv.strategy_family_key, csv.strategy_preset_key,
       csv.schedule_key, csv.strategy_fingerprint, csv.rule_graph,
       cmi.preset_key aggregation_preset_key, cmi.family_key aggregation_family_key
FROM strategy.compiled_strategy_version csv
JOIN lineage.artifact a ON a.artifact_id = csv.artifact_id
JOIN workspace.compiled_model_instance cmi
  ON cmi.compiled_model_instance_id = csv.compiled_model_instance_id
ORDER BY csv.created_at, csv.compiled_strategy_version_id
"""

CELL_RESULT_SQL = """
SELECT a.artifact_id::text artifact_id, a.status artifact_status,
       a.semantic_fingerprint, a.content_hash, cr.result_type,
       cr.result_fingerprint, cr.availability_status, cr.quality_status,
       cr.payload_content_hash, cr.payload_storage_format,
       cr.payload_schema_version, cr.payload_byte_size
FROM experiment.cell_result cr
JOIN lineage.artifact a ON a.artifact_id = cr.artifact_id
ORDER BY cr.result_type, cr.result_fingerprint, cr.artifact_id
"""

ACTIVE_PRODUCT_SQL = """
SELECT pv.artifact_id::text artifact_id, a.status artifact_status,
       a.semantic_fingerprint, a.content_hash, pe.product_enrollment_id::text,
       pe.lifecycle, pe.health, pv.product_key, pv.version_number,
       pv.product_fingerprint, csv.strategy_fingerprint,
       cmi.compiled_model_instance_id::text, cmi.preset_key, cmi.family_key,
       cmi.slot_assignments, crs.specification_fingerprint
FROM product.product_enrollment pe
JOIN product.product_version pv ON pv.product_version_id = pe.product_version_id
JOIN lineage.artifact a ON a.artifact_id = pv.artifact_id
JOIN strategy.compiled_strategy_version csv
  ON csv.compiled_strategy_version_id = pv.compiled_strategy_version_id
JOIN workspace.compiled_model_instance cmi
  ON cmi.compiled_model_instance_id = csv.compiled_model_instance_id
JOIN workspace.compiled_research_spec crs
  ON crs.compiled_research_spec_id = csv.compiled_research_spec_id
WHERE pe.lifecycle = 'active'
ORDER BY pv.product_key, pv.version_number, pe.product_enrollment_id
"""

PRODUCT_CLOSURE_SQL = """
WITH RECURSIVE roots AS (
  SELECT pe.product_enrollment_id root_key, pv.artifact_id
  FROM product.product_enrollment pe
  JOIN product.product_version pv ON pv.product_version_id = pe.product_version_id
  WHERE pe.lifecycle = 'active'
), closure(root_key, artifact_id, path) AS (
  SELECT root_key, artifact_id, ARRAY[artifact_id] FROM roots
  UNION ALL
  SELECT c.root_key, d.depends_on_artifact_id, c.path || d.depends_on_artifact_id
  FROM closure c
  JOIN lineage.artifact_dependency d ON d.artifact_id = c.artifact_id
  WHERE NOT d.depends_on_artifact_id = ANY(c.path)
)
SELECT DISTINCT a.artifact_id::text artifact_id, a.status artifact_status,
       a.semantic_fingerprint, a.content_hash, a.artifact_type, a.artifact_key,
       a.version_number
FROM closure c
JOIN lineage.artifact a ON a.artifact_id = c.artifact_id
ORDER BY artifact_type, artifact_key, version_number, artifact_id
"""

PRODUCT_EDGE_SQL = """
WITH RECURSIVE roots AS (
  SELECT pv.artifact_id
  FROM product.product_enrollment pe
  JOIN product.product_version pv ON pv.product_version_id = pe.product_version_id
  WHERE pe.lifecycle = 'active'
), closure(artifact_id, path) AS (
  SELECT artifact_id, ARRAY[artifact_id] FROM roots
  UNION ALL
  SELECT d.depends_on_artifact_id, c.path || d.depends_on_artifact_id
  FROM closure c
  JOIN lineage.artifact_dependency d ON d.artifact_id = c.artifact_id
  WHERE NOT d.depends_on_artifact_id = ANY(c.path)
)
SELECT DISTINCT d.artifact_id::text artifact_id, d.depends_on_artifact_id::text depends_on,
       d.role, d.ordinal
FROM lineage.artifact_dependency d
WHERE d.artifact_id IN (SELECT artifact_id FROM closure)
ORDER BY artifact_id, ordinal, depends_on
"""
