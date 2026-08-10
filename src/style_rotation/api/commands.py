from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import Engine, bindparam, text

from style_rotation.api.query import ArtifactQueryService
from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.experiment.history import ExperimentHistoryService
from style_rotation.experiment.suite_submission import (
    FormalExecutionEvidence,
    FormalSubmissionBlocked,
    ResearchSuiteSubmissionService,
)
from style_rotation.lineage.service import ArtifactService, DependencyInput
from style_rotation.ops.idempotency import CommandIdempotencyService
from style_rotation.ops.maintenance import suite_generation_guard
from style_rotation.product.alert_service import ProductAlertService
from style_rotation.product.lifecycle_service import ProductLifecycleService
from style_rotation.product.promotion import ProductPromotionService
from style_rotation.product.review_service import ProductReviewService, ReviewDecision
from style_rotation.workspace.contracts import CompiledResearchSpec
from style_rotation.workspace.drafts import ResearchDraftService
from style_rotation.workspace.materialization import WorkspaceSignalMaterializer
from style_rotation.workspace.release_gates import ReleaseGateEvidenceService, ReleaseGateStatus


class ApplicationCommandService:
    def __init__(
        self,
        engine: Engine,
        *,
        gate_provider: Callable[[], ReleaseGateStatus] | None = None,
        evidence_provider: Callable[[], FormalExecutionEvidence | None] | None = None,
    ) -> None:
        release_evidence = ReleaseGateEvidenceService(engine)
        self._engine = engine
        resolved_gate_provider = gate_provider or release_evidence.current_status
        self._reader = ArtifactQueryService(engine)
        self._idempotency = CommandIdempotencyService(engine)
        self._drafts = ResearchDraftService(engine)
        self._gate_provider = resolved_gate_provider
        self._evidence_provider = evidence_provider or (
            lambda: _formal_evidence(engine, release_evidence)
        )
        self._suites = ResearchSuiteSubmissionService(engine, gate_provider=resolved_gate_provider)
        self._experiment_history = ExperimentHistoryService(engine)
        self._promotion = ProductPromotionService(engine, gate_provider=resolved_gate_provider)
        self._lifecycle = ProductLifecycleService(engine)
        self._alerts = ProductAlertService(engine)
        self._reviews = ProductReviewService(engine)

    def idempotent(
        self,
        *,
        command_name: str,
        idempotency_key: uuid.UUID,
        request: dict[str, Any],
        operation: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        """Execute a mutating command exactly once for a client-supplied key."""
        return self._idempotency.execute(
            command_name=command_name,
            idempotency_key=idempotency_key,
            request=request,
            operation=operation,
        )

    def get_workspace_draft(self, *, researcher_id: str, draft_key: str) -> dict[str, Any]:
        draft = self._drafts.get(researcher_id=researcher_id, draft_key=draft_key)
        if draft is None:
            raise LookupError(f"Workspace draft not found: {researcher_id}/{draft_key}")
        return draft.to_dict()

    def save_workspace_draft(
        self,
        *,
        researcher_id: str,
        draft_key: str,
        name: str,
        selection: dict[str, Any],
        expected_revision: int | None,
    ) -> dict[str, Any]:
        return self._drafts.save(
            researcher_id=researcher_id,
            draft_key=draft_key,
            name=name,
            selection=selection,
            expected_revision=expected_revision,
        ).to_dict()

    def submit_workspace_draft(
        self,
        *,
        researcher_id: str,
        draft_key: str,
        expected_revision: int,
        suite_mode: Literal["formal", "exploratory"] = "formal",
    ) -> dict[str, Any]:
        gates = self._gate_provider()
        if suite_mode == "formal" and not gates.formal_enabled:
            raise FormalSubmissionBlocked(gates.reason_codes)
        draft = self._drafts.get(researcher_id=researcher_id, draft_key=draft_key)
        if draft is None:
            raise LookupError(f"Workspace draft not found: {researcher_id}/{draft_key}")
        if draft.revision != expected_revision:
            from style_rotation.workspace.drafts import DraftRevisionConflict

            raise DraftRevisionConflict(f"Draft revision conflict: current={draft.revision}")
        selection = draft.selection
        preview = self._reader.workspace_compile_preview(
            frequency=selection["frequency"],
            asset_security_ids=tuple(uuid.UUID(item) for item in selection["asset_security_ids"]),
            asset_data_inputs={
                str(security_id): tuple(input_keys)
                for security_id, input_keys in selection["asset_data_inputs"].items()
            },
            factor_variant_keys=tuple(selection["factor_variant_keys"]),
            signal_version_keys=tuple(selection["signal_version_keys"]),
            model_preset_keys=tuple(selection["model_preset_keys"]),
            model_target_keys=tuple(selection.get(
                "model_target_keys", ["cross_sectional_relative_return__h5"]
            )),
            strategy_preset_keys=tuple(selection["strategy_preset_keys"]),
        )
        if preview["blockers"]:
            raise ValueError("Draft contains blocked Model or Strategy selections")
        evidence = (
            self._evidence_provider()
            if suite_mode == "formal"
            else _exploratory_evidence(self._engine, preview["compiled"], selection)
        )
        if evidence is None:
            raise RuntimeError("Formal execution evidence is not configured")
        with suite_generation_guard(self._engine):
            result = self._suites.submit(
                compiled=CompiledResearchSpec.model_validate(preview["compiled"]),
                normalized_selection=selection,
                evidence=evidence,
                submission_key=f"{draft.research_draft_id}:{expected_revision}",
            )
            self._drafts.mark_compiled(
                researcher_id=researcher_id,
                draft_key=draft_key,
                expected_revision=expected_revision,
                artifact_id=result.suite_artifact_id,
            )
            self._experiment_history.prune_non_product_suites(
                retain_suite_id=result.research_suite_id
            )
        return result.to_dict()

    def suite_status(self, research_suite_id: uuid.UUID) -> dict[str, Any]:
        return self._suites.status(research_suite_id)

    def cancel_suite(self, research_suite_id: uuid.UUID) -> dict[str, Any]:
        cancelled = self._suites.cancel(research_suite_id)
        return {"research_suite_id": str(research_suite_id), "affected_work_items": cancelled}

    def evaluate_promotion(self, result_artifact_id: uuid.UUID) -> dict[str, Any]:
        return self._promotion.evaluate(result_artifact_id).to_dict()

    def promote_result(
        self,
        result_artifact_id: uuid.UUID,
        *,
        name: str,
        researcher_id: str,
        selection_reason: str,
        note: str | None,
    ) -> dict[str, Any]:
        return self._promotion.promote(
            result_artifact_id,
            name=name,
            researcher_id=researcher_id,
            selection_reason=selection_reason,
            note=note,
        ).to_dict()

    def change_product_lifecycle(
        self,
        enrollment_id: uuid.UUID,
        *,
        target: Literal["active", "suspended", "retired", "invalidated"],
        expected_revision: int,
        reason_code: str,
        reason: str,
        researcher_id: str,
        requested_at: datetime,
        effective_at: datetime,
    ) -> dict[str, Any]:
        if target not in {"active", "suspended", "retired", "invalidated"}:
            raise ValueError("Unsupported Product lifecycle target")
        change = self._lifecycle.change(
            enrollment_id,
            target=target,
            expected_revision=expected_revision,
            reason_code=reason_code,
            reason=reason,
            researcher_id=researcher_id,
            requested_at=requested_at,
            effective_at=effective_at,
        )
        return {
            "enrollment_id": str(change.enrollment_id),
            "from_lifecycle": change.from_lifecycle,
            "to_lifecycle": change.to_lifecycle,
            "revision": change.revision,
            "event_sequence": change.event_sequence,
            "effective_at": change.effective_at,
            "applied": change.applied,
        }

    def change_product_alert(
        self,
        alert_id: uuid.UUID,
        *,
        target: Literal["acknowledged", "resolved", "superseded"],
        researcher_id: str,
        note: str | None,
        occurred_at: datetime,
    ) -> dict[str, Any]:
        change = self._alerts.change(
            alert_id,
            target=target,
            researcher_id=researcher_id,
            note=note,
            occurred_at=occurred_at,
        )
        return {
            "alert_id": str(change.alert_id),
            "from_status": change.from_status,
            "to_status": change.to_status,
            "sequence_number": change.sequence_number,
            "occurred_at": change.occurred_at,
        }

    def record_product_review(
        self,
        enrollment_id: uuid.UUID,
        *,
        decision: ReviewDecision,
        researcher_id: str,
        reason: str,
        evidence: dict[str, Any],
        reviewed_at: datetime,
    ) -> dict[str, Any]:
        result = self._reviews.record(
            enrollment_id,
            decision=decision,
            researcher_id=researcher_id,
            reason=reason,
            evidence=evidence,
            reviewed_at=reviewed_at,
        )
        return {
            "product_review_id": str(result.product_review_id),
            "product_enrollment_id": str(result.product_enrollment_id),
            "decision": result.decision,
            "reviewed_at": result.reviewed_at,
        }

    def release_gates(self) -> dict[str, object]:
        return self._gate_provider().to_dict()

def _formal_evidence(
    engine: Engine, service: ReleaseGateEvidenceService
) -> FormalExecutionEvidence | None:
    evidence = service.active_evidence()
    if set(evidence) != {"pit_universe", "terminal_event", "impact_policy"}:
        return None
    impact = evidence["impact_policy"]
    context_artifact_id = uuid.UUID(str(impact["comparison_context_artifact_id"]))
    with engine.connect() as connection:
        fingerprint = connection.execute(
            text("""
            SELECT context_fingerprint FROM experiment.comparison_context context
            JOIN lineage.artifact artifact ON artifact.artifact_id = context.artifact_id
            WHERE context.artifact_id = :artifact_id AND artifact.status = 'published'
        """),
            {"artifact_id": context_artifact_id},
        ).scalar_one_or_none()
    if fingerprint is None:
        return None
    return FormalExecutionEvidence(
        comparison_context_fingerprint=fingerprint,
        impact_policy_key=str(impact.get("policy_key", "v021_impact_policy")),
        impact_coefficient=Decimal(str(impact["coefficient"])),
        impact_maximum_bps=Decimal(str(impact["maximum_bps"])),
        comparison_context_artifact_id=context_artifact_id,
        pit_gate_artifact_id=evidence["pit_universe"]["gate_artifact_id"],
        terminal_gate_artifact_id=evidence["terminal_event"]["gate_artifact_id"],
        impact_gate_artifact_id=impact["gate_artifact_id"],
        defensive_basket_version=str(
            impact.get(
                "defensive_basket_version",
                "standard_defensive_basket_long_history_v1",
            )
        ),
    )


def _exploratory_evidence(
    engine: Engine, compiled: dict[str, Any], selection: dict[str, Any]
) -> FormalExecutionEvidence:
    """Freeze a truthful local research context without claiming Formal gate evidence."""
    frequency = str(selection["frequency"])
    security_ids = tuple(uuid.UUID(str(item)) for item in selection["asset_security_ids"])
    with engine.connect() as connection:
        asset_ids = tuple(
            connection.execute(
                text(
                    "SELECT legacy_asset_id FROM catalog.security "
                    "WHERE security_id IN :security_ids AND legacy_asset_id IS NOT NULL"
                ).bindparams(bindparam("security_ids", expanding=True)),
                {"security_ids": security_ids},
            ).scalars()
        )
    if len(asset_ids) != len(security_ids):
        raise ValueError("Every selected Security must map to canonical market data")
    bundle_version_id, bundle_artifact_id = WorkspaceSignalMaterializer(
        engine
    ).latest_compatible_bundle(asset_ids)
    bundle = {
        "data_bundle_version_id": bundle_version_id,
        "artifact_id": bundle_artifact_id,
    }
    with engine.connect() as connection:
        coverage = (
            connection.execute(
                text("""
                SELECT min(bar.session_date) AS start_date, max(bar.session_date) AS end_date
                FROM data.data_bundle_member member
                JOIN data.daily_bar bar
                  ON bar.dataset_publication_id = member.dataset_publication_id
                WHERE member.data_bundle_version_id = :bundle_id
            """),
                {"bundle_id": bundle["data_bundle_version_id"]},
            )
            .mappings()
            .one()
        )
    if coverage["start_date"] is None or coverage["end_date"] is None:
        raise ValueError("Selected Data Bundle has no canonical market bars")

    artifacts = ArtifactService(engine)
    benchmark_payload = {
        "primary": "SPY",
        "research": "equal_weight_selected_universe",
        "suite_mode": "exploratory",
    }

    def write_benchmark(connection: Any, artifact_id: uuid.UUID) -> None:
        connection.execute(
            text("""
                INSERT INTO experiment.benchmark_set (
                    benchmark_set_id, artifact_id, benchmark_set_key, version_number,
                    primary_benchmark_key, research_benchmark_key, execution_policy
                ) VALUES (:id, :artifact_id, :key, 1, 'SPY',
                          'equal_weight_selected_universe', CAST(:policy AS jsonb))
            """),
            {
                "id": uuid.uuid4(),
                "artifact_id": artifact_id,
                "key": "v021_exploratory_benchmarks",
                "policy": __import__("json").dumps(benchmark_payload),
            },
        )

    benchmark = artifacts.publish(
        artifact_type="benchmark_set",
        artifact_key="v021_exploratory_benchmarks",
        version_number=1,
        semantic_payload=benchmark_payload,
        content_payload=benchmark_payload,
        draft_writer=write_benchmark,
    )
    with engine.connect() as connection:
        benchmark_id = connection.execute(
            text("SELECT benchmark_set_id FROM experiment.benchmark_set WHERE artifact_id=:id"),
            {"id": benchmark.artifact_id},
        ).scalar_one()

    universe_payload = {
        "asset_context_key": compiled["asset_context_key"],
        "asset_security_ids": sorted(str(item) for item in selection["asset_security_ids"]),
        "membership_semantics": "static_selected_universe_non_pit",
    }
    universe_key = f"exploratory_universe__{compiled['asset_context_key'][:24]}"
    universe = artifacts.publish(
        artifact_type="exploratory_universe_snapshot",
        artifact_key=universe_key,
        version_number=1,
        semantic_payload=universe_payload,
        content_payload=universe_payload,
    )
    context_payload = {
        "suite_mode": "exploratory",
        "data_bundle_artifact_id": str(bundle["artifact_id"]),
        "universe_artifact_id": str(universe.artifact_id),
        "benchmark_artifact_id": str(benchmark.artifact_id),
        "resolved_start": coverage["start_date"].isoformat(),
        "resolved_end": coverage["end_date"].isoformat(),
        "frequency": frequency,
        "cost_semantics": "linear_bps_only_no_market_impact",
    }
    fingerprint = sha256_hexdigest(context_payload)

    def write_context(connection: Any, artifact_id: uuid.UUID) -> None:
        connection.execute(
            text("""
                INSERT INTO experiment.comparison_context (
                    comparison_context_id, artifact_id, benchmark_set_id,
                    data_bundle_artifact_id, universe_history_artifact_id,
                    context_fingerprint, resolved_start, resolved_end, as_of_date,
                    state_reset_at, accounting_policy_key, metric_catalog_key
                ) VALUES (:id, :artifact_id, :benchmark_id, :bundle_id, :universe_id,
                          :fingerprint, :start_date, :end_date, :as_of_date,
                          :start_date, 'v021_exploratory_static_membership',
                          'v021_standard_performance_metrics')
            """),
            {
                "id": uuid.uuid4(),
                "artifact_id": artifact_id,
                "benchmark_id": benchmark_id,
                "bundle_id": bundle["artifact_id"],
                "universe_id": universe.artifact_id,
                "fingerprint": fingerprint,
                "start_date": coverage["start_date"],
                "end_date": coverage["end_date"],
                "as_of_date": max(datetime.now(UTC).date(), coverage["end_date"]),
            },
        )

    context = artifacts.publish(
        artifact_type="comparison_context",
        artifact_key=fingerprint,
        version_number=1,
        semantic_payload=context_payload,
        content_payload=context_payload,
        dependencies=(
            DependencyInput(benchmark.artifact_id, "benchmark_set"),
            DependencyInput(bundle["artifact_id"], "data_bundle"),
            DependencyInput(universe.artifact_id, "exploratory_universe"),
        ),
        draft_writer=write_context,
    )
    return FormalExecutionEvidence(
        comparison_context_fingerprint=fingerprint,
        impact_policy_key="exploratory_linear_bps_only",
        impact_coefficient=Decimal("1"),
        impact_maximum_bps=Decimal("1"),
        comparison_context_artifact_id=context.artifact_id,
        pit_gate_artifact_id=None,
        terminal_gate_artifact_id=None,
        impact_gate_artifact_id=None,
        suite_mode="exploratory",
    )
