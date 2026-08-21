from __future__ import annotations

import csv
import io
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated, Any, Literal, Protocol, cast

from fastapi import FastAPI, Header, HTTPException, Query, Request, Response
from fastapi import Path as ApiPath
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from style_rotation import __version__
from style_rotation.api.actor_context import (
    ActorClaimMismatch,
    ActorContext,
    ActorRoleDenied,
    TrustedLocalActorContext,
    require_actor_claim,
    require_actor_role,
)
from style_rotation.api.commands import ApplicationCommandService
from style_rotation.api.query import ArtifactQueryService
from style_rotation.api.schemas import (
    ApiContext,
    ApiError,
    ArtifactDetailResponse,
    ArtifactListResponse,
    ArtifactSummary,
    AssetCatalogResponse,
    AssetDataExportJobResponse,
    AssetDataExportPreviewRequest,
    AssetDataExportPreviewResponse,
    AssetSeriesResponse,
    CapabilitiesResponse,
    CommandIdempotencyRequest,
    DataOverviewResponse,
    DataRequirementResponse,
    DatasetPublicationItem,
    DecisionExplorerResponse,
    DependencySummary,
    DomainCapability,
    ExperimentOverviewResponse,
    ExperimentResultResponse,
    FactorDatasetDiagnosticItem,
    FactorOverviewResponse,
    GraphCatalogRebasePreviewRequest,
    GraphChangeConfirmRequest,
    GraphChangePreviewRequest,
    GraphChangePreviewResponse,
    GraphDraftCloneRequest,
    GraphDraftCompileRequest,
    GraphDraftCompileResponse,
    GraphDraftCreateRequest,
    GraphDraftEventRequest,
    GraphDraftResetRequest,
    GraphDraftResetSummaryResponse,
    GraphDraftSnapshotResponse,
    GraphStageFamilyPageResponse,
    GraphSuiteLaunchBatchRequest,
    GraphSuiteLaunchBatchResponse,
    GraphSuiteListResponse,
    GraphSuiteResultsResponse,
    GraphSuiteRuntimeReadinessResponse,
    GraphSuiteStatusResponse,
    GraphSuiteSubmitRequest,
    GraphSuiteSubmitResponse,
    GraphWorkspacePreviewRequest,
    GraphWorkspacePreviewResponse,
    HealthResponse,
    LineageManifestResponse,
    ModelDiagnosticItem,
    ModelOverviewResponse,
    ProductAlertChangeRequest,
    ProductAlertChangeResponse,
    ProductCatalogResponse,
    ProductCompareResponse,
    ProductDetailResponse,
    ProductLifecycleChangeRequest,
    ProductLifecycleChangeResponse,
    ProductPromotionRequest,
    ProductPromotionResponse,
    ProductRankingResponse,
    ProductRecommendationResponse,
    ProductReviewRequest,
    ProductReviewResponse,
    PromotionQualificationResponse,
    QualityState,
    QualitySummary,
    ReleaseGateResponse,
    SessionContextResponse,
    SignalDiagnosticItem,
    SignalOverviewResponse,
    SignalResearchExportJobResponse,
    SignalResearchExportRequest,
    StrategyOverviewResponse,
    StrategyTargetPathResponse,
    V022ExperimentIdentityCatalogResponse,
    V022ExperimentIdentityDetailResponse,
    V022ExperimentLeaderboardResponse,
    V022ExperimentSeriesResponse,
    V022ProductCandidateRequest,
    V022ProductCandidateResponse,
    V022ProductEnrollmentRequest,
    V022ProductEnrollmentResponse,
    V022ProductIdentityCatalogResponse,
    V022ProductIdentityDetailResponse,
    V022ProductLifecycleRequest,
    V022ProductLifecycleResponse,
    V022ProductPromotionResponse,
    V022ReleaseControlResponse,
    WorkspaceCompilePreviewResponse,
    WorkspaceCompileRequest,
    WorkspaceDraftResponse,
    WorkspaceDraftSaveRequest,
    WorkspaceOptionsResponse,
    WorkspaceSuiteCancelResponse,
    WorkspaceSuiteStatusResponse,
    WorkspaceSuiteSubmitRequest,
    WorkspaceSuiteSubmitResponse,
)
from style_rotation.architecture import DOMAIN_BOUNDARIES
from style_rotation.config.settings import get_settings
from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.experiment.suite_submission import FormalSubmissionBlocked
from style_rotation.ops.idempotency import CommandIdempotencyService, IdempotencyConflict
from style_rotation.persistence.session import create_postgres_engine
from style_rotation.product.lifecycle_service import ProductRevisionConflict
from style_rotation.product.recommendation import ProductRecommendationService
from style_rotation.signal.export_jobs import SignalExportJob, SignalResearchExportJobService
from style_rotation.v022.asset_data_export import (
    AssetDataExportJob,
    AssetDataExportPreview,
    AssetDataExportService,
)
from style_rotation.v022.draft_service import (
    CascadeConfirmationRequired,
    ChangePreviewExpired,
    GraphCatalogRebaseRequired,
    GraphDraftCompilationBlocked,
    GraphDraftIdempotencyConflict,
    GraphDraftLocked,
    GraphDraftRevisionConflict,
    GraphDraftService,
    GraphDraftSnapshot,
    GraphViewTokenConflict,
    GraphWorkspaceViewIncompatible,
)
from style_rotation.v022.mutation_admission import (
    MutationAdmissionDenied,
    MutationAdmissionService,
    MutationScope,
)
from style_rotation.v022.product_candidate import ProductCandidateService
from style_rotation.v022.product_enrollment_command import (
    ProductEnrollmentCommand,
    ProductEnrollmentCommandService,
)
from style_rotation.v022.product_monitoring import (
    EnrollmentLifecycleService,
    LifecycleSequenceConflict,
)
from style_rotation.v022.product_promotion import ProductPromotionService
from style_rotation.v022.product_runtime import DecisionSessionInput
from style_rotation.v022.release_control import (
    LocalDevelopmentReleaseControlService,
    ReleaseControlService,
)
from style_rotation.v022.suite_launch_batch import (
    SuiteLaunchBatchRequest,
    SuiteLaunchBatchService,
    SuiteLaunchBatchStageError,
)
from style_rotation.v022.suite_runtime_commands import (
    GraphSuiteCommandsAdapter,
    GraphSuiteResultsNotReady,
    SuiteRuntimeCommandService,
)
from style_rotation.v022.suite_worker_readiness import LocalSuiteWorkerHeartbeat
from style_rotation.v022.workspace_view import (
    ExplicitFeature,
    GraphWorkspacePreviewService,
    WorkspacePreviewIntent,
    representative_workspace_service,
)
from style_rotation.workspace.drafts import DraftRevisionConflict
from style_rotation.workspace.release_gates import current_release_gates

ARTIFACT_STATUSES = ("draft", "published", "retired", "superseded", "invalidated", "tainted")
INTERFACE_STATES = (
    "loading",
    "empty",
    "partial",
    "warning",
    "error",
    "stale",
    "tainted",
    "invalidated",
)
DEFAULT_STATIC_DIR = Path(__file__).parents[3] / "frontend" / "dist"


class ArtifactReader(Protocol):
    def database_revision(self) -> str | None: ...
    def list_artifacts(
        self,
        *,
        statuses: Sequence[str],
        artifact_type: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[dict[str, Any]], int]: ...
    def artifact_detail(self, artifact_id: uuid.UUID) -> dict[str, Any]: ...
    def lineage_manifest(self, artifact_id: uuid.UUID) -> dict[str, Any]: ...
    def asset_catalog(
        self,
        *,
        search: str | None,
        category: str | None,
        maturity: str | None,
        tradability: str | None,
        limit: int,
        offset: int,
    ) -> dict[str, Any]: ...
    def asset_series(
        self, security_id: uuid.UUID, *, start: str | None, end: str | None
    ) -> dict[str, Any]: ...
    def workspace_options(
        self,
        *,
        frequency: str,
        selected_factor_variants: tuple[str, ...],
        selected_signals: tuple[str, ...],
        selected_models: tuple[str, ...] = (),
        selected_strategies: tuple[str, ...] = (),
        selected_assets: tuple[uuid.UUID, ...] = (),
        selected_asset_data_inputs: dict[str, tuple[str, ...]] | None = None,
    ) -> dict[str, Any]: ...
    def workspace_compile_preview(
        self,
        *,
        frequency: str,
        asset_security_ids: tuple[uuid.UUID, ...],
        asset_data_inputs: dict[str, tuple[str, ...]],
        factor_variant_keys: tuple[str, ...],
        signal_version_keys: tuple[str, ...],
        model_preset_keys: tuple[str, ...],
        model_target_keys: tuple[str, ...],
        strategy_preset_keys: tuple[str, ...],
    ) -> dict[str, Any]: ...
    def factor_values(self, artifact_id: uuid.UUID) -> dict[str, Any]: ...
    def signal_values(self, version_key: str) -> dict[str, Any]: ...
    def product_catalog(self) -> dict[str, Any]: ...
    def product_detail(self, enrollment_id: uuid.UUID) -> dict[str, Any]: ...
    def data_requirements(self) -> dict[str, Any]: ...
    def data_overview(self) -> dict[str, Any]: ...
    def factor_overview(self) -> dict[str, Any]: ...
    def signal_overview(self, frequency: str) -> dict[str, Any]: ...
    def model_overview(self, frequency: str) -> dict[str, Any]: ...
    def strategy_overview(self) -> dict[str, Any]: ...
    def strategy_target_path(self, artifact_id: uuid.UUID) -> dict[str, Any]: ...
    def experiment_overview(
        self,
        *,
        research_suite_id: uuid.UUID | None,
        status: str | None,
        template_key: str | None,
        frequency: str | None,
        cost_bps_per_side: float | None,
        ranking_metric: str,
        limit: int,
        offset: int,
    ) -> dict[str, Any]: ...
    def experiment_result(self, artifact_id: uuid.UUID) -> dict[str, Any]: ...
    def v022_experiment_identity_catalog(self) -> dict[str, Any]: ...
    def v022_experiment_identity_detail(self, evidence_id: uuid.UUID) -> dict[str, Any]: ...
    def v022_experiment_result_series(
        self, evidence_id: uuid.UUID, *, max_points: int
    ) -> dict[str, Any]: ...
    def v022_experiment_leaderboard(
        self,
        *,
        frequency: str,
        ranking_cohort_release_id: uuid.UUID | None,
        sort: str,
        limit: int,
        offset: int,
    ) -> dict[str, Any]: ...
    def v022_product_identity_catalog(self) -> dict[str, Any]: ...
    def v022_product_identity_detail(self, enrollment_id: uuid.UUID) -> dict[str, Any]: ...
    def product_ranking(
        self, *, cohort_artifact_id: uuid.UUID | None, metric_key: str
    ) -> dict[str, Any]: ...
    def product_compare(self, *, result_artifact_ids: tuple[uuid.UUID, ...]) -> dict[str, Any]: ...
    def decision_explorer(
        self, *, result_artifact_id: uuid.UUID, decision_date: date | None
    ) -> dict[str, Any]: ...


def _context() -> ApiContext:
    return ApiContext(system_version=__version__)


def _parse_asset_data_input_query(
    selected_assets: tuple[uuid.UUID, ...], encoded: list[str] | None
) -> dict[str, tuple[str, ...]] | None:
    """Decode repeated ``<security-id>:<input-key>`` options, preserving empty choices."""
    if encoded is None:
        return None
    selected = {str(security_id) for security_id in selected_assets}
    parsed: dict[str, list[str]] = {security_id: [] for security_id in selected}
    for item in encoded:
        security_id, separator, input_key = item.partition(":")
        if not separator or security_id not in selected:
            raise HTTPException(status_code=422, detail="Invalid selected asset data-input option")
        if input_key and input_key not in parsed[security_id]:
            parsed[security_id].append(input_key)
    return {key: tuple(value) for key, value in parsed.items()}


def _quality(status: str) -> QualitySummary:
    mapping: dict[str, tuple[QualityState, list[str]]] = {
        "draft": ("partial", ["artifact.draft"]),
        "published": ("ok", []),
        "retired": ("warning", ["artifact.retired"]),
        "superseded": ("warning", ["artifact.superseded"]),
        "tainted": ("warning", ["artifact.tainted"]),
        "invalidated": ("error", ["artifact.invalidated"]),
    }
    state, codes = mapping[status]
    return QualitySummary(state=state, codes=codes)


def _signal_export_job_response(job: SignalExportJob) -> SignalResearchExportJobResponse:
    status_url = f"/api/v2/signals/research-exports/{job.export_job_id}"
    downloadable = (
        job.status == "completed"
        and job.expires_at is not None
        and job.expires_at > datetime.now(UTC)
    )
    quality_by_status: dict[str, QualityState] = {
        "queued": "partial",
        "running": "partial",
        "completed": "ok",
        "failed": "error",
        "cancelled": "warning",
    }
    quality_state = quality_by_status[job.status]
    return SignalResearchExportJobResponse(
        context=_context(),
        quality=QualitySummary(
            state=quality_state,
            codes=(
                [f"signal.export_{job.status}"]
                if job.status in {"failed", "cancelled"}
                else []
            ),
        ),
        export_job_id=job.export_job_id,
        work_item_id=job.work_item_id,
        request_fingerprint=job.request_fingerprint,
        status=job.status,
        stage=job.stage,
        attempt_count=job.attempt_count,
        max_attempts=job.max_attempts,
        status_url=status_url,
        download_url=f"{status_url}/download" if downloadable else None,
        failure_class=job.failure_class,
        failure_details=job.failure_details,
        content_hash=job.content_hash,
        byte_size=job.byte_size,
        expires_at=job.expires_at,
    )


def _asset_data_export_job_response(job: AssetDataExportJob) -> AssetDataExportJobResponse:
    status_url = f"/api/v2/v022/asset-data-exports/{job.export_job_id}"
    downloadable = (
        job.status == "completed"
        and job.expires_at is not None
        and job.expires_at > datetime.now(UTC)
    )
    quality_by_status: dict[str, QualityState] = {
        "queued": "partial",
        "running": "partial",
        "completed": "ok",
        "failed": "error",
        "cancelled": "warning",
    }
    return AssetDataExportJobResponse(
        context=_context(),
        quality=QualitySummary(
            state=quality_by_status[job.status],
            codes=(
                [f"asset.export_{job.status}"]
                if job.status in {"failed", "cancelled"}
                else []
            ),
        ),
        export_job_id=job.export_job_id,
        work_item_id=job.work_item_id,
        status=job.status,
        stage=job.stage,
        processed_rows=job.processed_rows,
        processed_bytes=job.processed_bytes,
        total_rows=job.total_rows,
        estimated_bytes=job.estimated_bytes,
        request_fingerprint=job.request_fingerprint,
        status_url=status_url,
        download_url=f"{status_url}/download" if downloadable else None,
        content_hash=job.content_hash,
        byte_size=job.byte_size,
        filename=job.filename,
        expires_at=job.expires_at,
        local_delivery_path=job.local_delivery_path,
        error_code=job.error_code,
        error_message=job.error_message,
    )


def _asset_data_export_preview_response(
    preview: AssetDataExportPreview,
) -> AssetDataExportPreviewResponse:
    return AssetDataExportPreviewResponse(
        context=_context(),
        quality=QualitySummary(state="warning", codes=list(preview.warning_codes)),
        graph_draft_id=preview.graph_draft_id,
        graph_draft_revision=preview.graph_draft_revision,
        asset_registry_release_id=preview.asset_registry_release_id,
        dataset_publication_id=preview.dataset_publication_id,
        dataset_gate_assessment_id=preview.dataset_gate_assessment_id,
        dataset_key=preview.dataset_key,
        dataset_version_number=preview.dataset_version_number,
        price_semantics=preview.price_semantics,
        asset_count=len(preview.security_ids),
        start_date=preview.start_date,
        end_date=preview.end_date,
        row_count=preview.row_count,
        estimated_bytes=preview.estimated_bytes,
        export_format=preview.export_format,
        fields=list(preview.fields),
        warning_codes=list(preview.warning_codes),
        request_fingerprint=preview.request_fingerprint,
    )


def _artifact(payload: dict[str, Any]) -> ArtifactSummary:
    return ArtifactSummary.model_validate({**payload, "quality": _quality(str(payload["status"]))})


def _etag_response(
    payload: object, response: Response, if_none_match: str | None
) -> Response | None:
    hash_payload = payload.model_dump(mode="json") if isinstance(payload, BaseModel) else payload
    etag = f'"{sha256_hexdigest(hash_payload)}"'
    if if_none_match == etag:
        return Response(status_code=304, headers={"ETag": etag})
    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = "private, max-age=0, must-revalidate"
    return None


class GraphSuiteCommands(Protocol):
    """Public v0.22 Suite boundary, intentionally separate from legacy commands."""

    def replay(
        self,
        *,
        actor_key: str,
        idempotency_key: uuid.UUID,
        compiled_research_graph_id: uuid.UUID,
        suite_mode: Literal["exploratory"],
    ) -> dict[str, Any] | None: ...

    def submit(
        self,
        *,
        actor_key: str,
        idempotency_key: uuid.UUID,
        compiled_research_graph_id: uuid.UUID,
        suite_mode: Literal["exploratory"],
    ) -> dict[str, Any]: ...

    def status(self, research_suite_id: uuid.UUID) -> dict[str, Any]: ...

    def results(self, research_suite_id: uuid.UUID) -> dict[str, Any]: ...

    def list_suites(self, *, limit: int, offset: int) -> dict[str, Any]: ...


class GraphSuiteLaunchBatches(Protocol):
    def submit(self, request: SuiteLaunchBatchRequest) -> dict[str, Any]: ...

    def status(self, suite_launch_batch_id: uuid.UUID) -> dict[str, Any]: ...


def _production_graph_suite_commands(
    engine: Any, *, payload_directory: str | Path
) -> GraphSuiteCommands:
    """Publish Suite identities quickly; the durable worker owns all execution."""

    # Retain the argument at the application boundary for deployment compatibility;
    # payload storage is now owned by the independent runtime worker.
    del payload_directory
    return GraphSuiteCommandsAdapter(SuiteRuntimeCommandService(engine))


def create_app(
    reader: ArtifactReader | None = None,
    *,
    static_directory: Path | None = None,
    commands: ApplicationCommandService | None = None,
    signal_export_jobs: SignalResearchExportJobService | None = None,
    asset_data_exports: AssetDataExportService | None = None,
    graph_workspace: GraphWorkspacePreviewService | None = None,
    graph_drafts: GraphDraftService | None = None,
    graph_suites: GraphSuiteCommands | None = None,
    graph_suite_batches: GraphSuiteLaunchBatches | None = None,
    v022_product_candidates: ProductCandidateService | None = None,
    v022_product_enrollments: ProductEnrollmentCommandService | None = None,
    v022_product_promotions: ProductPromotionService | None = None,
    v022_product_lifecycle: EnrollmentLifecycleService | None = None,
    v022_command_idempotency: CommandIdempotencyService | None = None,
    mutation_admission: MutationAdmissionService | None = None,
    release_control: ReleaseControlService | None = None,
    actor_context: ActorContext | None = None,
    suite_worker_heartbeat: LocalSuiteWorkerHeartbeat | None = None,
) -> FastAPI:
    application_engine = None
    if reader is None:
        application_engine = create_postgres_engine(get_settings().database_url)
        reader = ArtifactQueryService(application_engine)
    if commands is None and application_engine is not None:
        commands = ApplicationCommandService(application_engine)
    if signal_export_jobs is None and application_engine is not None:
        signal_export_jobs = SignalResearchExportJobService(
            application_engine, directory=get_settings().signal_export_directory
        )
    if asset_data_exports is None and application_engine is not None:
        asset_data_exports = AssetDataExportService(application_engine)
    if graph_workspace is None:
        graph_workspace = representative_workspace_service()
    if graph_drafts is None and application_engine is not None:
        graph_drafts = GraphDraftService(application_engine, graph_workspace)
    enforce_suite_worker_readiness = graph_suites is None and application_engine is not None
    if graph_suites is None and application_engine is not None:
        graph_suites = _production_graph_suite_commands(
            application_engine,
            payload_directory=get_settings().v022_payload_directory,
        )
    if (
        graph_suite_batches is None
        and application_engine is not None
        and graph_drafts is not None
        and graph_suites is not None
    ):
        graph_suite_batches = SuiteLaunchBatchService(
            application_engine,
            graph_drafts=graph_drafts,
            graph_suites=graph_suites,
        )
    runtime_heartbeat = suite_worker_heartbeat or LocalSuiteWorkerHeartbeat()
    if v022_product_candidates is None and application_engine is not None:
        v022_product_candidates = ProductCandidateService(application_engine)
    if v022_product_enrollments is None and application_engine is not None:
        v022_product_enrollments = ProductEnrollmentCommandService(application_engine)
    if v022_product_promotions is None and application_engine is not None:
        v022_product_promotions = ProductPromotionService(application_engine)
    if v022_product_lifecycle is None and application_engine is not None:
        v022_product_lifecycle = EnrollmentLifecycleService(application_engine)
    if v022_command_idempotency is None and application_engine is not None:
        v022_command_idempotency = CommandIdempotencyService(application_engine)
    if release_control is None and application_engine is not None:
        settings = get_settings()
        release_control = (
            LocalDevelopmentReleaseControlService(application_engine)
            if settings.environment == "local"
            and settings.v022_local_development_enabled
            else ReleaseControlService(application_engine)
        )
    if mutation_admission is None and application_engine is not None:
        mutation_admission = MutationAdmissionService(
            application_engine, release_control=release_control
        )
    if actor_context is None and application_engine is not None:
        settings = get_settings()
        actor_context = TrustedLocalActorContext(
            actor_key=settings.api_actor_key,
            operator_enabled=settings.api_operator_enabled,
        )
    product_recommendations = (
        ProductRecommendationService(application_engine) if application_engine is not None else None
    )
    static_dir = static_directory or DEFAULT_STATIC_DIR
    app = FastAPI(
        title="Style Rotation Research API",
        version=__version__,
        description=(
            "Local v0.22 graph research, lineage, product, and controlled command interface "
            "with pinned v0.21 compatibility."
        ),
        docs_url="/api/v2/docs",
        redoc_url=None,
        openapi_url="/api/v2/openapi.json",
    )
    app.state.reader = reader

    @app.exception_handler(LookupError)
    async def lookup_error(_request: Request, error: LookupError) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content=ApiError(code="not_found", message=str(error)).model_dump(),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, error: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=ApiError(code="invalid_request", message=str(error)).model_dump(),
        )

    @app.exception_handler(HTTPException)
    async def http_error(_request: Request, error: HTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code,
            content=ApiError(code="invalid_request", message=str(error.detail)).model_dump(),
            headers=error.headers,
        )

    @app.exception_handler(DraftRevisionConflict)
    async def draft_conflict(_request: Request, error: DraftRevisionConflict) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content=ApiError(code="revision_conflict", message=str(error)).model_dump(),
        )

    @app.exception_handler(IdempotencyConflict)
    async def idempotency_conflict(_request: Request, error: IdempotencyConflict) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content=ApiError(code="idempotency_conflict", message=str(error)).model_dump(),
        )

    @app.exception_handler(SuiteLaunchBatchStageError)
    async def suite_launch_batch_stage_error(
        _request: Request, error: SuiteLaunchBatchStageError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content=ApiError(
                code=error.code,
                message=str(error),
                details={
                    "stage": error.stage,
                    "frequency": error.frequency,
                    "summary": error.summary,
                },
            ).model_dump(),
        )

    @app.exception_handler(FormalSubmissionBlocked)
    async def formal_submission_blocked(
        _request: Request, error: FormalSubmissionBlocked
    ) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content=ApiError(
                code="formal_submission_blocked",
                message=str(error),
                details={"reason_codes": list(error.reason_codes)},
            ).model_dump(),
        )

    @app.exception_handler(MutationAdmissionDenied)
    async def mutation_admission_denied(
        _request: Request, error: MutationAdmissionDenied
    ) -> JSONResponse:
        decision = error.decision
        return JSONResponse(
            status_code=423 if decision.release_state == "maintenance_read_only" else 409,
            content=ApiError(
                code="mutation_admission_blocked",
                message=str(error),
                details={
                    "scope": decision.scope,
                    "release_state": decision.release_state,
                    "reason_code": decision.reason_code,
                },
            ).model_dump(),
        )

    @app.exception_handler(ActorClaimMismatch)
    async def actor_claim_mismatch(
        _request: Request, error: ActorClaimMismatch
    ) -> JSONResponse:
        return JSONResponse(
            status_code=403,
            content=ApiError(
                code="actor_claim_mismatch",
                message=str(error),
                details={"authenticated_actor": error.authenticated_actor},
            ).model_dump(),
        )

    @app.exception_handler(ActorRoleDenied)
    async def actor_role_denied(_request: Request, error: ActorRoleDenied) -> JSONResponse:
        return JSONResponse(
            status_code=403,
            content=ApiError(
                code="actor_role_denied",
                message=str(error),
                details={"required_role": error.required_role},
            ).model_dump(),
        )

    def require_mutation(scope: MutationScope) -> None:
        if mutation_admission is not None:
            mutation_admission.require(scope)

    def admitted_operation(
        scope: MutationScope, operation: Callable[[], dict[str, Any]]
    ) -> dict[str, Any]:
        require_mutation(scope)
        return operation()

    def authenticated_actor(claimed_actor: str) -> str:
        return require_actor_claim(actor_context, claimed_actor)

    @app.exception_handler(ProductRevisionConflict)
    async def product_revision_conflict(
        _request: Request, error: ProductRevisionConflict
    ) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content=ApiError(code="product_revision_conflict", message=str(error)).model_dump(),
        )

    @app.exception_handler(LifecycleSequenceConflict)
    async def lifecycle_sequence_conflict(
        _request: Request, error: LifecycleSequenceConflict
    ) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content=ApiError(
                code="v022_lifecycle_sequence_conflict", message=str(error)
            ).model_dump(),
        )

    @app.exception_handler(GraphDraftRevisionConflict)
    async def graph_draft_revision_conflict(
        _request: Request, error: GraphDraftRevisionConflict
    ) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content=ApiError(
                code="draft_revision_conflict",
                message=str(error),
                details={"current_revision": error.current_revision},
            ).model_dump(),
        )

    @app.exception_handler(GraphDraftLocked)
    async def graph_draft_locked(
        _request: Request, error: GraphDraftLocked
    ) -> JSONResponse:
        return JSONResponse(
            status_code=423,
            content=ApiError(
                code="current_research_locked",
                message=str(error),
                details={"revision": error.revision},
            ).model_dump(),
        )

    @app.exception_handler(GraphCatalogRebaseRequired)
    async def graph_catalog_rebase_required(
        _request: Request, error: GraphCatalogRebaseRequired
    ) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content=ApiError(
                code="catalog_rebase_required",
                message=str(error),
                details={"current_catalog_release_id": str(error.current_catalog_release_id)},
            ).model_dump(),
        )

    @app.exception_handler(GraphViewTokenConflict)
    async def graph_view_token_conflict(
        _request: Request, error: GraphViewTokenConflict
    ) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content=ApiError(
                code="workspace_view_token_conflict",
                message=str(error),
                details={
                    "current_revision": error.current_revision,
                    "current_view_token": error.current_view_token,
                },
            ).model_dump(),
        )

    @app.exception_handler(GraphWorkspaceViewIncompatible)
    async def graph_workspace_view_incompatible(
        _request: Request, error: GraphWorkspaceViewIncompatible
    ) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content=ApiError(
                code="workspace_view_incompatible",
                message=str(error),
                details={
                    "revision": error.revision,
                    "reason_code": error.reason_code,
                },
            ).model_dump(),
        )

    @app.exception_handler(GraphDraftIdempotencyConflict)
    async def graph_draft_idempotency_conflict(
        _request: Request, error: GraphDraftIdempotencyConflict
    ) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content=ApiError(code="idempotency_payload_conflict", message=str(error)).model_dump(),
        )

    @app.exception_handler(GraphDraftCompilationBlocked)
    async def graph_draft_compilation_blocked(
        _request: Request, error: GraphDraftCompilationBlocked
    ) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content=ApiError(
                code="selection_conflict",
                message=str(error),
                details={"blockers": list(error.blockers)},
            ).model_dump(),
        )

    @app.exception_handler(GraphSuiteResultsNotReady)
    async def graph_suite_results_not_ready(
        _request: Request, error: GraphSuiteResultsNotReady
    ) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content=ApiError(
                code="graph_suite_results_not_ready",
                message=str(error),
                details={
                    "status": error.status,
                    "total": error.total,
                    "terminal": error.terminal,
                },
            ).model_dump(),
        )

    @app.exception_handler(CascadeConfirmationRequired)
    async def cascade_confirmation_required(
        _request: Request, error: CascadeConfirmationRequired
    ) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content=ApiError(
                code="cascade_confirmation_required",
                message=str(error),
                details={"locked_by": list(error.locked_by)},
            ).model_dump(),
        )

    @app.exception_handler(ChangePreviewExpired)
    async def change_preview_expired(
        _request: Request, error: ChangePreviewExpired
    ) -> JSONResponse:
        return JSONResponse(
            status_code=410,
            content=ApiError(code="impact_preview_expired", message=str(error)).model_dump(),
        )

    @app.exception_handler(ValueError)
    async def value_error(_request: Request, error: ValueError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=ApiError(code="invalid_request", message=str(error)).model_dump(),
        )

    @app.get("/api/v2/health", response_model=HealthResponse, tags=["system"])
    def health() -> HealthResponse:
        read_only = (
            release_control.current().maintenance_read_only
            if release_control is not None
            else False
        )
        return HealthResponse(
            context=ApiContext(system_version=__version__, read_only=read_only),
            quality=QualitySummary(state="ok"),
            database_revision=reader.database_revision(),
        )

    @app.get(
        "/api/v2/release-control",
        response_model=V022ReleaseControlResponse,
        tags=["system", "v0.22"],
    )
    def release_control_status() -> V022ReleaseControlResponse:
        if release_control is None:
            raise HTTPException(status_code=503, detail="Release Control unavailable")
        status = release_control.current()
        return V022ReleaseControlResponse(
            context=ApiContext(
                system_version=__version__, read_only=status.maintenance_read_only
            ),
            quality=QualitySummary(state="warning" if status.maintenance_read_only else "ok"),
            state=status.state,
            transition_sequence=status.transition_sequence,
            transition_artifact_id=status.transition_artifact_id,
            default_contract=status.default_contract,
            maintenance_read_only=status.maintenance_read_only,
            shadow_runtime_allowed=status.shadow_runtime_allowed,
            v021_research_creation_allowed=status.v021_research_creation_allowed,
            v022_explicit_creation_allowed=status.v022_explicit_creation_allowed,
        )

    @app.get("/api/v2/session", response_model=SessionContextResponse, tags=["system"])
    def session_context() -> SessionContextResponse:
        if actor_context is None:
            raise HTTPException(status_code=503, detail="Authenticated Actor unavailable")
        actor = actor_context.current()
        return SessionContextResponse(
            context=_context(),
            quality=QualitySummary(state="ok"),
            actor_key=actor.actor_key,
            roles=sorted(actor.roles),
            authentication_source=actor.authentication_source,
        )

    @app.get("/api/v2/capabilities", response_model=CapabilitiesResponse, tags=["system"])
    def capabilities() -> CapabilitiesResponse:
        available = {
            "catalog",
            "data",
            "factor",
            "signal",
            "model",
            "strategy",
            "experiment",
            "lineage",
            "ops",
        }
        return CapabilitiesResponse(
            context=_context(),
            quality=QualitySummary(state="ok"),
            domains=[
                DomainCapability(
                    key=item.key,
                    purpose=item.purpose,
                    upstream=list(item.upstream),
                    delivery_milestone=item.delivery_milestone,
                    availability="available" if item.key in available else "planned",
                )
                for item in DOMAIN_BOUNDARIES
            ],
            endpoints=[
                "health",
                "capabilities",
                "release_control",
                "session",
                "assets",
                "data_requirements",
                "data_overview",
                "factor_overview",
                "signal_overview",
                "model_overview",
                "strategy_overview",
                "strategy_target_path",
                "experiment_overview",
                "experiment_result",
                "product_ranking",
                "product_compare",
                "decision_explorer",
                "artifacts",
                "lineage",
            ],
            interface_states=list(INTERFACE_STATES),
            languages=["zh-CN", "en"],
        )

    @app.get(
        "/api/v2/experiments/overview",
        response_model=ExperimentOverviewResponse,
        responses={304: {"description": "Not modified"}},
        tags=["experiment"],
    )
    def experiment_overview(
        response: Response,
        research_suite_id: Annotated[uuid.UUID | None, Query()] = None,
        status: Annotated[
            Literal["accepted", "failed", "running", "pending"] | None, Query()
        ] = None,
        template_key: Annotated[str | None, Query(max_length=80)] = None,
        frequency: Annotated[Literal["weekly", "monthly"] | None, Query()] = None,
        cost_bps_per_side: Annotated[float | None, Query(ge=0, le=1000)] = None,
        ranking_metric: Annotated[
            Literal[
                "strategy.sharpe_ratio",
                "strategy.cagr",
                "strategy.maximum_drawdown",
                "relative.annualized_relative_wealth_growth",
                "predictive.mean_rank_ic",
            ],
            Query(),
        ] = "strategy.sharpe_ratio",
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
        if_none_match: Annotated[str | None, Header()] = None,
    ) -> ExperimentOverviewResponse | Response:
        result = reader.experiment_overview(
            research_suite_id=research_suite_id,
            status=status,
            template_key=template_key,
            frequency=frequency,
            cost_bps_per_side=cost_bps_per_side,
            ranking_metric=ranking_metric,
            limit=limit,
            offset=offset,
        )
        codes: list[str] = []
        if any(item["status"] == "failed" for item in result["specifications"]):
            codes.append("experiment.failed_cells")
        if any(item["availability_status"] == "excluded" for item in result["specifications"]):
            codes.append("experiment.excluded_cells")
        payload = ExperimentOverviewResponse.model_validate(
            {
                "context": _context(),
                "quality": QualitySummary(state="warning" if codes else "ok", codes=codes),
                **result,
            }
        )
        cached = _etag_response(payload, response, if_none_match)
        return cached or payload

    @app.get(
        "/api/v2/experiments/results/{artifact_id}",
        response_model=ExperimentResultResponse,
        responses={304: {"description": "Not modified"}},
        tags=["experiment"],
    )
    def experiment_result(
        artifact_id: uuid.UUID,
        response: Response,
        if_none_match: Annotated[str | None, Header()] = None,
    ) -> ExperimentResultResponse | Response:
        payload = ExperimentResultResponse.model_validate(
            {
                "context": _context(),
                "quality": QualitySummary(state="ok"),
                **reader.experiment_result(artifact_id),
            }
        )
        cached = _etag_response(payload, response, if_none_match)
        return cached or payload

    @app.get(
        "/api/v2/experiments/results/{artifact_id}/qualification",
        response_model=PromotionQualificationResponse,
        tags=["experiment", "product"],
    )
    def promotion_qualification(
        artifact_id: uuid.UUID,
    ) -> PromotionQualificationResponse:
        if commands is None:
            raise HTTPException(status_code=503, detail="Product commands unavailable")
        result = commands.evaluate_promotion(artifact_id)
        return PromotionQualificationResponse(
            context=_context(),
            quality=QualitySummary(state="ok" if result["eligible"] else "warning"),
            **result,
        )

    @app.get(
        "/api/v2/v022/experiments",
        response_model=V022ExperimentIdentityCatalogResponse,
        tags=["experiment", "v0.22"],
    )
    def v022_experiment_identities() -> V022ExperimentIdentityCatalogResponse:
        return V022ExperimentIdentityCatalogResponse(
            context=_context(),
            quality=QualitySummary(state="ok"),
            **reader.v022_experiment_identity_catalog(),
        )

    @app.get(
        "/api/v2/v022/experiments/leaderboard",
        response_model=V022ExperimentLeaderboardResponse,
        tags=["experiment", "v0.22"],
    )
    def v022_experiment_leaderboard(
        frequency: Annotated[Literal["weekly", "monthly"], Query()] = "weekly",
        ranking_cohort_release_id: uuid.UUID | None = None,
        sort: Annotated[
            Literal["sharpe_ratio", "cagr", "cagr_spread", "maximum_drawdown"],
            Query(),
        ] = "sharpe_ratio",
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> V022ExperimentLeaderboardResponse:
        return V022ExperimentLeaderboardResponse(
            context=_context(),
            quality=QualitySummary(state="ok"),
            **reader.v022_experiment_leaderboard(
                frequency=frequency,
                ranking_cohort_release_id=ranking_cohort_release_id,
                sort=sort,
                limit=limit,
                offset=offset,
            ),
        )

    @app.get(
        "/api/v2/v022/experiments/{evidence_id}",
        response_model=V022ExperimentIdentityDetailResponse,
        tags=["experiment", "v0.22"],
    )
    def v022_experiment_identity(
        evidence_id: uuid.UUID,
    ) -> V022ExperimentIdentityDetailResponse:
        return V022ExperimentIdentityDetailResponse(
            context=_context(),
            quality=QualitySummary(state="ok"),
            **reader.v022_experiment_identity_detail(evidence_id),
        )

    @app.get(
        "/api/v2/v022/experiments/{evidence_id}/series",
        response_model=V022ExperimentSeriesResponse,
        tags=["experiment", "v0.22"],
    )
    def v022_experiment_series(
        evidence_id: uuid.UUID,
        max_points: Annotated[int, Query(ge=2, le=2000)] = 600,
    ) -> V022ExperimentSeriesResponse:
        return V022ExperimentSeriesResponse(
            context=_context(),
            quality=QualitySummary(state="ok"),
            **reader.v022_experiment_result_series(
                evidence_id,
                max_points=max_points,
            ),
        )

    @app.post(
        "/api/v2/v022/experiments/{evidence_id}/promote",
        response_model=V022ProductCandidateResponse,
        tags=["experiment", "product", "v0.22"],
    )
    def promote_v022_product_candidate(
        evidence_id: uuid.UUID,
        request: V022ProductCandidateRequest,
    ) -> V022ProductCandidateResponse:
        if v022_product_candidates is None:
            raise HTTPException(status_code=503, detail="v0.22 Product candidates unavailable")
        actor_key = authenticated_actor(request.researcher_id)
        require_mutation("v022_research")
        return V022ProductCandidateResponse(
            context=_context(),
            quality=QualitySummary(
                state="warning",
                codes=["product.candidate_not_enrolled"],
            ),
            **v022_product_candidates.promote(
                result_evidence_snapshot_id=evidence_id,
                actor_key=actor_key,
                idempotency_key=request.idempotency_key,
                product_key=request.product_key,
                name=request.name,
                description=request.description,
                version_number=request.version_number,
            ),
        )

    @app.post(
        "/api/v2/v022/experiment-results/{evidence_id}/promote-and-enroll",
        response_model=V022ProductPromotionResponse,
        tags=["experiment", "product", "v0.22"],
    )
    def promote_and_enroll_v022_product(
        evidence_id: uuid.UUID,
        request: V022ProductCandidateRequest,
    ) -> V022ProductPromotionResponse:
        if v022_product_promotions is None:
            raise HTTPException(status_code=503, detail="v0.22 Product promotion unavailable")
        actor_key = authenticated_actor(request.researcher_id)
        require_mutation("v022_research")
        publication = v022_product_promotions.promote_and_enroll(
            result_evidence_snapshot_id=evidence_id,
            actor_key=actor_key,
            idempotency_key=request.idempotency_key,
            product_key=request.product_key,
            name=request.name,
            description=request.description,
            version_number=request.version_number,
        )
        return V022ProductPromotionResponse(
            context=_context(),
            quality=QualitySummary(
                state=(
                    "warning"
                    if publication["product_eligibility"] == "eligible_with_warnings"
                    else "ok"
                ),
                codes=list(publication["warning_codes"]),
            ),
            **publication,
        )

    @app.post(
        "/api/v2/v022/product-candidates/{execution_version_id}/enroll",
        response_model=V022ProductEnrollmentResponse,
        tags=["product", "v0.22"],
    )
    def enroll_v022_product_candidate(
        execution_version_id: uuid.UUID,
        request: V022ProductEnrollmentRequest,
    ) -> V022ProductEnrollmentResponse:
        if v022_product_enrollments is None:
            raise HTTPException(status_code=503, detail="v0.22 Product enrollment unavailable")
        actor_key = authenticated_actor(request.researcher_id)
        require_mutation("v022_research")
        return V022ProductEnrollmentResponse(
            context=_context(),
            quality=QualitySummary(
                state="warning",
                codes=["product.market_price_mapping_required"],
            ),
            **v022_product_enrollments.enroll(
                command=ProductEnrollmentCommand(
                    execution_version_id=execution_version_id,
                    qualification_version_id=request.qualification_version_id,
                    monitoring_policy_version_id=request.monitoring_policy_version_id,
                    schedule_key=request.schedule_key,
                    schedule_version_number=request.schedule_version_number,
                    frequency=request.frequency,
                    sessions=tuple(
                        DecisionSessionInput(item.session_date, item.decision_cutoff_at)
                        for item in request.sessions
                    ),
                    oos_anchor_cutoff_at=request.oos_anchor_cutoff_at,
                    activation_effective_at=request.activation_effective_at,
                ),
                actor_key=actor_key,
                idempotency_key=request.idempotency_key,
            ),
        )

    @app.post(
        "/api/v2/experiments/results/{artifact_id}/promote",
        response_model=ProductPromotionResponse,
        tags=["experiment", "product"],
    )
    def promote_experiment_result(
        artifact_id: uuid.UUID,
        request: ProductPromotionRequest,
    ) -> ProductPromotionResponse:
        if commands is None:
            raise HTTPException(status_code=503, detail="Product commands unavailable")
        actor_key = authenticated_actor(request.researcher_id)
        return ProductPromotionResponse(
            context=_context(),
            quality=QualitySummary(state="ok"),
            **commands.idempotent(
                command_name="promote_experiment_result",
                idempotency_key=request.idempotency_key,
                request={"artifact_id": str(artifact_id), **request.model_dump(mode="json")},
                operation=lambda: admitted_operation(
                    "v021_research",
                    lambda: commands.promote_result(
                        artifact_id,
                        name=request.name,
                        researcher_id=actor_key,
                        selection_reason=request.selection_reason,
                        note=request.note,
                    ),
                ),
            ),
        )

    @app.get(
        "/api/v2/rankings/products",
        response_model=ProductRankingResponse,
        responses={304: {"description": "Not modified"}},
        tags=["experiment"],
    )
    def product_ranking(
        response: Response,
        cohort_artifact_id: Annotated[uuid.UUID | None, Query()] = None,
        metric: Annotated[
            Literal[
                "net_sharpe",
                "net_cagr",
                "relative_wealth_growth",
                "maximum_drawdown",
                "calmar",
            ],
            Query(),
        ] = "net_sharpe",
        if_none_match: Annotated[str | None, Header()] = None,
    ) -> ProductRankingResponse | Response:
        result = reader.product_ranking(cohort_artifact_id=cohort_artifact_id, metric_key=metric)
        payload = ProductRankingResponse.model_validate(
            {
                "context": _context(),
                "quality": QualitySummary(state="ok"),
                **result,
            }
        )
        cached = _etag_response(payload, response, if_none_match)
        return cached or payload

    @app.get(
        "/api/v2/compare/products",
        response_model=ProductCompareResponse,
        responses={304: {"description": "Not modified"}},
        tags=["experiment"],
    )
    def product_compare(
        response: Response,
        result_artifact_id: Annotated[list[uuid.UUID], Query(min_length=2, max_length=6)],
        if_none_match: Annotated[str | None, Header()] = None,
    ) -> ProductCompareResponse | Response:
        if len(set(result_artifact_id)) != len(result_artifact_id):
            raise HTTPException(status_code=422, detail="Product Compare results must be distinct")
        result = reader.product_compare(result_artifact_ids=tuple(result_artifact_id))
        codes = [] if result["mode"] == "controlled" else [f"compare.{result['mode']}"]
        payload = ProductCompareResponse.model_validate(
            {
                "context": _context(),
                "quality": QualitySummary(state="ok" if not codes else "warning", codes=codes),
                **result,
            }
        )
        cached = _etag_response(payload, response, if_none_match)
        return cached or payload

    @app.get(
        "/api/v2/experiments/results/{artifact_id}/decisions",
        response_model=DecisionExplorerResponse,
        responses={304: {"description": "Not modified"}},
        tags=["experiment"],
    )
    def decision_explorer(
        artifact_id: uuid.UUID,
        response: Response,
        decision_date: Annotated[date | None, Query()] = None,
        if_none_match: Annotated[str | None, Header()] = None,
    ) -> DecisionExplorerResponse | Response:
        payload = DecisionExplorerResponse.model_validate(
            {
                "context": _context(),
                "quality": QualitySummary(state="ok"),
                **reader.decision_explorer(
                    result_artifact_id=artifact_id, decision_date=decision_date
                ),
            }
        )
        cached = _etag_response(payload, response, if_none_match)
        return cached or payload

    @app.get(
        "/api/v2/strategies/overview",
        response_model=StrategyOverviewResponse,
        responses={304: {"description": "Not modified"}},
        tags=["strategy"],
    )
    def strategy_overview(
        response: Response,
        if_none_match: Annotated[str | None, Header()] = None,
    ) -> StrategyOverviewResponse | Response:
        payload = StrategyOverviewResponse.model_validate(
            {
                "context": _context(),
                "quality": QualitySummary(state="ok"),
                **reader.strategy_overview(),
            }
        )
        cached = _etag_response(payload, response, if_none_match)
        return cached or payload

    @app.get(
        "/api/v2/strategies/targets/{artifact_id}",
        response_model=StrategyTargetPathResponse,
        responses={304: {"description": "Not modified"}},
        tags=["strategy"],
    )
    def strategy_target_path(
        artifact_id: uuid.UUID,
        response: Response,
        if_none_match: Annotated[str | None, Header()] = None,
    ) -> StrategyTargetPathResponse | Response:
        payload = StrategyTargetPathResponse.model_validate(
            {
                "context": _context(),
                "quality": QualitySummary(state="ok"),
                **reader.strategy_target_path(artifact_id),
            }
        )
        cached = _etag_response(payload, response, if_none_match)
        return cached or payload

    @app.get(
        "/api/v2/artifacts",
        response_model=ArtifactListResponse,
        responses={304: {"description": "Not modified"}},
        tags=["lineage"],
    )
    def artifacts(
        response: Response,
        status: Annotated[list[str] | None, Query()] = None,
        artifact_type: Annotated[str | None, Query(min_length=1, max_length=80)] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
        if_none_match: Annotated[str | None, Header()] = None,
    ) -> ArtifactListResponse | Response:
        statuses = status or ["published"]
        unknown = sorted(set(statuses).difference(ARTIFACT_STATUSES))
        if unknown:
            raise HTTPException(status_code=422, detail=f"Unknown artifact status: {unknown}")
        rows, total = reader.list_artifacts(
            statuses=statuses, artifact_type=artifact_type, limit=limit, offset=offset
        )
        items = [_artifact(row) for row in rows]
        quality = QualitySummary(
            state="warning" if any(item.quality.state != "ok" for item in items) else "ok",
            codes=sorted({code for item in items for code in item.quality.codes}),
        )
        payload = ArtifactListResponse(
            context=_context(),
            quality=quality,
            items=items,
            total=total,
            limit=limit,
            offset=offset,
        )
        cached = _etag_response(payload, response, if_none_match)
        return cached or payload

    @app.get(
        "/api/v2/models/overview",
        response_model=ModelOverviewResponse,
        responses={304: {"description": "Not modified"}},
        tags=["model"],
    )
    def model_overview(
        response: Response,
        frequency: Annotated[Literal["weekly", "monthly"], Query()] = "weekly",
        if_none_match: Annotated[str | None, Header()] = None,
    ) -> ModelOverviewResponse | Response:
        result = reader.model_overview(frequency)
        issue_severities: dict[str, set[str]] = {}
        for issue in result["issues"]:
            issue_severities.setdefault(str(issue["specification_key"]), set()).add(
                str(issue["severity"])
            )
        models: list[ModelDiagnosticItem] = []
        has_error = False
        has_warning = False
        for model in result["models"]:
            severities = issue_severities.get(str(model["specification_key"]), set())
            if "error" in severities:
                quality = QualitySummary(state="error", codes=["model.diagnostic_error"])
                has_error = True
            elif "warning" in severities:
                quality = QualitySummary(state="warning", codes=["model.diagnostic_warning"])
                has_warning = True
            else:
                quality = QualitySummary(state="ok")
            models.append(ModelDiagnosticItem.model_validate({**model, "quality": quality}))
        if has_error:
            overall = QualitySummary(state="error", codes=["model.diagnostic_error"])
        elif has_warning:
            overall = QualitySummary(state="warning", codes=["model.diagnostic_warning"])
        else:
            overall = QualitySummary(state="ok")
        payload = ModelOverviewResponse.model_validate(
            {"context": _context(), "quality": overall, **result, "models": models}
        )
        cached = _etag_response(payload, response, if_none_match)
        return cached or payload

    @app.get(
        "/api/v2/signals/overview",
        response_model=SignalOverviewResponse,
        responses={304: {"description": "Not modified"}},
        tags=["signal"],
    )
    def signal_overview(
        response: Response,
        frequency: Annotated[Literal["weekly", "monthly"], Query()] = "weekly",
        if_none_match: Annotated[str | None, Header()] = None,
    ) -> SignalOverviewResponse | Response:
        result = reader.signal_overview(frequency)
        issue_severities: dict[str, set[str]] = {}
        for issue in result["issues"]:
            issue_severities.setdefault(str(issue["signal_key"]), set()).add(str(issue["severity"]))
        signals: list[SignalDiagnosticItem] = []
        has_error = False
        has_warning = False
        for signal in result["signals"]:
            severities = issue_severities.get(str(signal["signal_key"]), set())
            if "error" in severities:
                quality = QualitySummary(state="error", codes=["signal.diagnostic_error"])
                has_error = True
            elif "warning" in severities:
                quality = QualitySummary(state="warning", codes=["signal.diagnostic_warning"])
                has_warning = True
            else:
                quality = QualitySummary(state="ok")
            signals.append(SignalDiagnosticItem.model_validate({**signal, "quality": quality}))
        if has_error:
            overall = QualitySummary(state="error", codes=["signal.diagnostic_error"])
        elif has_warning:
            overall = QualitySummary(state="warning", codes=["signal.diagnostic_warning"])
        else:
            overall = QualitySummary(state="ok")
        payload = SignalOverviewResponse.model_validate(
            {"context": _context(), "quality": overall, **result, "signals": signals}
        )
        cached = _etag_response(payload, response, if_none_match)
        return cached or payload

    @app.get(
        "/api/v2/data/overview",
        response_model=DataOverviewResponse,
        responses={304: {"description": "Not modified"}},
        tags=["data"],
    )
    def data_overview(
        response: Response,
        if_none_match: Annotated[str | None, Header()] = None,
    ) -> DataOverviewResponse | Response:
        result = reader.data_overview()
        dataset_payloads: list[dict[str, Any]] = []
        quality_codes: set[str] = set()
        has_error = False
        has_warning = False
        for dataset in result["datasets"]:
            severities = {issue["severity"] for issue in dataset["issues"]}
            if "error" in severities:
                dataset_quality = QualitySummary(state="error", codes=["data.quality_error"])
                has_error = True
            elif "warning" in severities:
                dataset_quality = QualitySummary(state="warning", codes=["data.quality_warning"])
                has_warning = True
            else:
                dataset_quality = QualitySummary(state="ok")
            quality_codes.update(dataset_quality.codes)
            dataset_payloads.append({**dataset, "quality": dataset_quality})
        eligibility = result["eligibility"]
        if eligibility and eligibility["eligible_count"] < eligibility["member_count"]:
            has_warning = True
            quality_codes.add("data.ineligible_assets")
        incomplete = not result["datasets"] or result["bundle"] is None or eligibility is None
        if has_error:
            overall_state: QualityState = "error"
        elif incomplete:
            quality_codes.add("data.incomplete_chain")
            overall_state = "partial"
        elif has_warning:
            overall_state = "warning"
        else:
            overall_state = "ok"
        payload = DataOverviewResponse(
            context=_context(),
            quality=QualitySummary(state=overall_state, codes=sorted(quality_codes)),
            sources=result["sources"],
            datasets=[DatasetPublicationItem.model_validate(item) for item in dataset_payloads],
            bundle=result["bundle"],
            eligibility=eligibility,
        )
        cached = _etag_response(payload, response, if_none_match)
        return cached or payload

    @app.get(
        "/api/v2/factors/overview",
        response_model=FactorOverviewResponse,
        responses={304: {"description": "Not modified"}},
        tags=["factor"],
    )
    def factor_overview(
        response: Response,
        if_none_match: Annotated[str | None, Header()] = None,
    ) -> FactorOverviewResponse | Response:
        result = reader.factor_overview()
        issue_severities: dict[str, set[str]] = {}
        for issue in result["issues"]:
            issue_severities.setdefault(str(issue["variant_key"]), set()).add(
                str(issue["severity"])
            )
        datasets: list[FactorDatasetDiagnosticItem] = []
        has_error = False
        has_warning = False
        for dataset in result["datasets"]:
            severities = issue_severities.get(str(dataset["variant_key"]), set())
            if "error" in severities:
                quality = QualitySummary(state="error", codes=["factor.diagnostic_error"])
                has_error = True
            elif "warning" in severities:
                quality = QualitySummary(state="warning", codes=["factor.diagnostic_warning"])
                has_warning = True
            else:
                quality = QualitySummary(state="ok")
            datasets.append(
                FactorDatasetDiagnosticItem.model_validate({**dataset, "quality": quality})
            )
        if has_error:
            overall_quality = QualitySummary(state="error", codes=["factor.diagnostic_error"])
        elif has_warning:
            overall_quality = QualitySummary(state="warning", codes=["factor.diagnostic_warning"])
        else:
            overall_quality = QualitySummary(state="ok")
        payload = FactorOverviewResponse.model_validate(
            {
                "context": _context(),
                "quality": overall_quality,
                **result,
                "datasets": datasets,
            }
        )
        cached = _etag_response(payload, response, if_none_match)
        return cached or payload

    @app.get(
        "/api/v2/catalog/assets",
        response_model=AssetCatalogResponse,
        responses={304: {"description": "Not modified"}},
        tags=["catalog"],
    )
    def asset_catalog(
        response: Response,
        search: Annotated[str | None, Query(max_length=200)] = None,
        category: Annotated[str | None, Query(max_length=80)] = None,
        maturity: Annotated[
            Literal[
                "cataloged",
                "reference_data",
                "canonical_ready",
                "research_ready",
                "strategy_ready",
                "product_eligible_input",
            ]
            | None,
            Query(),
        ] = None,
        tradability: Annotated[
            Literal["tradable", "reference_only", "synthetic"] | None, Query()
        ] = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 60,
        offset: Annotated[int, Query(ge=0)] = 0,
        if_none_match: Annotated[str | None, Header()] = None,
    ) -> AssetCatalogResponse | Response:
        payload = AssetCatalogResponse(
            context=_context(),
            quality=QualitySummary(state="ok"),
            **reader.asset_catalog(
                search=search,
                category=category,
                maturity=maturity,
                tradability=tradability,
                limit=limit,
                offset=offset,
            ),
        )
        cached = _etag_response(payload, response, if_none_match)
        return cached or payload

    @app.get(
        "/api/v2/catalog/assets/{security_id}/series",
        response_model=AssetSeriesResponse,
        tags=["catalog"],
    )
    def asset_series(
        security_id: uuid.UUID,
        start: Annotated[date | None, Query()] = None,
        end: Annotated[date | None, Query()] = None,
    ) -> AssetSeriesResponse:
        if start is not None and end is not None and start > end:
            raise HTTPException(status_code=422, detail="start must be on or before end")
        return AssetSeriesResponse(
            context=_context(),
            quality=QualitySummary(state="ok"),
            **reader.asset_series(
                security_id,
                start=start.isoformat() if start else None,
                end=end.isoformat() if end else None,
            ),
        )

    @app.get("/api/v2/catalog/assets/{security_id}/download.csv", tags=["catalog"])
    def asset_download(
        security_id: uuid.UUID,
        start: Annotated[date | None, Query()] = None,
        end: Annotated[date | None, Query()] = None,
    ) -> Response:
        if start is not None and end is not None and start > end:
            raise HTTPException(status_code=422, detail="start must be on or before end")
        series = reader.asset_series(
            security_id,
            start=start.isoformat() if start else None,
            end=end.isoformat() if end else None,
        )
        output = io.StringIO(newline="")
        writer = csv.DictWriter(
            output,
            fieldnames=["session_date", "open", "high", "low", "close", "adjusted_close", "volume"],
        )
        writer.writeheader()
        writer.writerows(series["points"])
        filename = f"{series['asset_key']}_canonical.csv"
        return Response(
            content=output.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "X-Dataset-Artifact-Id": str(series["dataset_artifact_id"]),
            },
        )

    @app.get(
        "/api/v2/workspace/options",
        response_model=WorkspaceOptionsResponse,
        tags=["workspace"],
    )
    def workspace_options(
        frequency: Annotated[Literal["weekly", "monthly"], Query()] = "weekly",
        selected_factor_variant: Annotated[list[str] | None, Query()] = None,
        selected_signal: Annotated[list[str] | None, Query()] = None,
        selected_model: Annotated[list[str] | None, Query()] = None,
        selected_strategy: Annotated[list[str] | None, Query()] = None,
        selected_asset: Annotated[list[uuid.UUID] | None, Query()] = None,
        selected_asset_data_input: Annotated[list[str] | None, Query()] = None,
    ) -> WorkspaceOptionsResponse:
        asset_inputs = _parse_asset_data_input_query(
            tuple(selected_asset or ()), selected_asset_data_input
        )
        return WorkspaceOptionsResponse(
            context=_context(),
            quality=QualitySummary(state="ok"),
            **reader.workspace_options(
                frequency=frequency,
                selected_factor_variants=tuple(selected_factor_variant or ()),
                selected_signals=tuple(selected_signal or ()),
                selected_models=tuple(selected_model or ()),
                selected_strategies=tuple(selected_strategy or ()),
                selected_assets=tuple(selected_asset or ()),
                selected_asset_data_inputs=asset_inputs,
            ),
        )

    @app.post(
        "/api/v2/workspace/compile-preview",
        response_model=WorkspaceCompilePreviewResponse,
        tags=["workspace"],
    )
    def workspace_compile_preview(
        request: WorkspaceCompileRequest,
    ) -> WorkspaceCompilePreviewResponse:
        return WorkspaceCompilePreviewResponse(
            context=_context(),
            quality=QualitySummary(state="ok"),
            **reader.workspace_compile_preview(
                frequency=request.frequency,
                asset_security_ids=tuple(request.asset_security_ids),
                asset_data_inputs={
                    str(security_id): tuple(input_keys)
                    for security_id, input_keys in request.asset_data_inputs.items()
                },
                factor_variant_keys=tuple(request.factor_variant_keys),
                signal_version_keys=tuple(request.signal_version_keys),
                model_preset_keys=tuple(request.model_preset_keys),
                model_target_keys=tuple(request.model_target_keys),
                strategy_preset_keys=tuple(request.strategy_preset_keys),
            ),
        )

    @app.post(
        "/api/v2/workspace/graph-preview",
        response_model=GraphWorkspacePreviewResponse,
        tags=["workspace-v022"],
    )
    def graph_workspace_preview(
        request: GraphWorkspacePreviewRequest,
    ) -> GraphWorkspacePreviewResponse:
        payload = graph_workspace.preview(
            WorkspacePreviewIntent(
                explicit_features=tuple(
                    ExplicitFeature(item.feature_key, item.stage_no)
                    for item in request.explicit_features
                ),
                aggregation_family_keys=tuple(request.aggregation_family_keys),
                frequency=request.frequency,
                aggregation_parameter_preset_keys=tuple(
                    (key, tuple(values))
                    for key, values in sorted(request.aggregation_parameter_preset_keys.items())
                ),
                strategy_keys=tuple(request.strategy_keys),
                defense_keys=tuple(request.defense_keys),
                strategy_parameter_preset_keys=tuple(
                    (key, tuple(values))
                    for key, values in sorted(
                        request.strategy_parameter_preset_keys.items()
                    )
                ),
            )
        )
        return GraphWorkspacePreviewResponse(
            context=_context(),
            quality=QualitySummary(
                state="warning" if payload["blockers"] else "ok",
                codes=[
                    reason for blocker in payload["blockers"] for reason in blocker["reason_codes"]
                ],
            ),
            **payload,
        )

    def graph_draft_response(
        snapshot: GraphDraftSnapshot,
        *,
        applied: bool | None = None,
        reset_summary: dict[str, object] | None = None,
    ) -> GraphDraftSnapshotResponse:
        blockers = snapshot.derived_view["blockers"]
        payload = snapshot.to_dict()
        derived_view = dict(snapshot.derived_view)
        derived_view["stages"] = [
            {**stage, "families": []} for stage in snapshot.derived_view["stages"]
        ]
        payload["derived_view"] = derived_view
        return GraphDraftSnapshotResponse(
            context=_context(),
            quality=QualitySummary(
                state="warning" if blockers else "ok",
                codes=[reason for blocker in blockers for reason in blocker["reason_codes"]],
            ),
            **payload,
            applied=applied,
            reset_summary=(
                GraphDraftResetSummaryResponse.model_validate(reset_summary)
                if reset_summary is not None
                else None
            ),
        )

    @app.post(
        "/api/v2/workspace/graph-drafts",
        response_model=GraphDraftSnapshotResponse,
        tags=["workspace-v022"],
    )
    def create_graph_draft(request: GraphDraftCreateRequest) -> GraphDraftSnapshotResponse:
        if graph_drafts is None:
            raise HTTPException(status_code=503, detail="Graph Draft commands unavailable")
        actor_key = authenticated_actor(request.researcher_key)
        require_mutation("v022_research")
        snapshot = graph_drafts.create(
            researcher_key=actor_key,
            draft_key=request.draft_key,
            name=request.name,
            idempotency_key=request.idempotency_key,
            frequency=request.frequency,
            asset_context_key=request.asset_context_key,
            data_input_keys=tuple(request.data_input_keys),
        )
        return graph_draft_response(snapshot)

    @app.get(
        "/api/v2/workspace/graph-drafts/by-key/{draft_key}",
        response_model=GraphDraftSnapshotResponse,
        tags=["workspace-v022"],
    )
    def get_graph_draft_by_key(draft_key: str) -> GraphDraftSnapshotResponse:
        if graph_drafts is None:
            raise HTTPException(status_code=503, detail="Graph Draft commands unavailable")
        if actor_context is None:
            raise HTTPException(status_code=503, detail="Actor context unavailable")
        actor_key = require_actor_role(actor_context, required_role="researcher")
        return graph_draft_response(
            graph_drafts.get_by_key(researcher_key=actor_key, draft_key=draft_key)
        )

    @app.get(
        "/api/v2/workspace/graph-drafts/{graph_draft_id}",
        response_model=GraphDraftSnapshotResponse,
        tags=["workspace-v022"],
    )
    def get_graph_draft(graph_draft_id: uuid.UUID) -> GraphDraftSnapshotResponse:
        if graph_drafts is None:
            raise HTTPException(status_code=503, detail="Graph Draft commands unavailable")
        return graph_draft_response(graph_drafts.get(graph_draft_id))

    @app.get(
        "/api/v2/workspace/graph-drafts/{graph_draft_id}/current-compile",
        response_model=GraphDraftCompileResponse,
        tags=["workspace-v022"],
    )
    def get_current_graph_draft_compile(
        graph_draft_id: uuid.UUID,
    ) -> GraphDraftCompileResponse:
        if graph_drafts is None:
            raise HTTPException(status_code=503, detail="Graph Draft commands unavailable")
        if actor_context is None:
            raise HTTPException(status_code=503, detail="Actor context unavailable")
        actor_key = require_actor_role(actor_context, required_role="researcher")
        outcome = graph_drafts.current_compile(
            graph_draft_id,
            actor_key=actor_key,
        )
        if outcome is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "current_compile_not_found",
                    "message": "The current Graph Draft revision has not been compiled",
                },
            )
        return GraphDraftCompileResponse(
            context=_context(), quality=QualitySummary(state="ok"), **outcome.to_dict()
        )

    @app.post(
        "/api/v2/workspace/graph-drafts/{graph_draft_id}/clones",
        response_model=GraphDraftSnapshotResponse,
        tags=["workspace-v022"],
    )
    def clone_graph_draft_revision(
        graph_draft_id: uuid.UUID, request: GraphDraftCloneRequest
    ) -> GraphDraftSnapshotResponse:
        if graph_drafts is None:
            raise HTTPException(status_code=503, detail="Graph Draft commands unavailable")
        actor_key = authenticated_actor(request.researcher_key)
        require_mutation("v022_research")
        return graph_draft_response(
            graph_drafts.clone_revision(
                graph_draft_id,
                source_revision=request.source_revision,
                researcher_key=actor_key,
                draft_key=request.draft_key,
                name=request.name,
                idempotency_key=request.idempotency_key,
            )
        )

    @app.get(
        "/api/v2/workspace/graph-drafts/{graph_draft_id}/stages/{stage_no}/families",
        response_model=GraphStageFamilyPageResponse,
        tags=["workspace-v022"],
    )
    def graph_draft_stage_families(
        graph_draft_id: uuid.UUID,
        stage_no: Annotated[int, ApiPath(ge=0, le=3)],
        search: str = Query(default="", max_length=240),
        selection_filter: Literal["all", "selected", "locked"] = "all",
        availability_filter: Literal[
            "all", "ready", "requires_ancestors", "hard_incompatible"
        ] = "all",
        cursor: str | None = Query(default=None, max_length=800),
        limit: int = Query(default=12, ge=1, le=50),
    ) -> GraphStageFamilyPageResponse:
        if graph_drafts is None:
            raise HTTPException(status_code=503, detail="Graph Draft commands unavailable")
        actor_key = (
            require_actor_role(actor_context, required_role="researcher")
            if actor_context is not None
            else None
        )
        page = graph_drafts.stage_families(
            graph_draft_id,
            stage_no=cast(Literal[0, 1, 2, 3], stage_no),
            search=search,
            selection_filter=selection_filter,
            availability_filter=availability_filter,
            cursor=cursor,
            limit=limit,
            actor_key=actor_key,
        )
        return GraphStageFamilyPageResponse(
            context=_context(), quality=QualitySummary(state="ok"), **page.to_dict()
        )

    @app.post(
        "/api/v2/workspace/graph-drafts/{graph_draft_id}/events",
        response_model=GraphDraftSnapshotResponse,
        tags=["workspace-v022"],
    )
    def apply_graph_draft_event(
        graph_draft_id: uuid.UUID, request: GraphDraftEventRequest
    ) -> GraphDraftSnapshotResponse:
        if graph_drafts is None:
            raise HTTPException(status_code=503, detail="Graph Draft commands unavailable")
        actor_key = authenticated_actor(request.actor_key)
        require_mutation("v022_research")
        result = graph_drafts.apply_event(
            graph_draft_id,
            expected_revision=request.expected_revision,
            actor_key=actor_key,
            idempotency_key=request.idempotency_key,
            event_type=request.event_type,
            event=request.event,
        )
        return graph_draft_response(result.snapshot, applied=result.applied)

    @app.post(
        "/api/v2/workspace/graph-drafts/{graph_draft_id}/reset",
        response_model=GraphDraftSnapshotResponse,
        tags=["workspace-v022"],
    )
    def reset_graph_draft(
        graph_draft_id: uuid.UUID, request: GraphDraftResetRequest
    ) -> GraphDraftSnapshotResponse:
        if graph_drafts is None:
            raise HTTPException(status_code=503, detail="Graph Draft commands unavailable")
        actor_key = authenticated_actor(request.actor_key)
        require_mutation("v022_research")
        result = graph_drafts.reset_current_research(
            graph_draft_id,
            expected_revision=request.expected_revision,
            actor_key=actor_key,
            idempotency_key=request.idempotency_key,
        )
        return graph_draft_response(
            result.snapshot,
            reset_summary={
                "closed_research_round_id": result.closed_research_round_id,
                "opened_research_round_id": result.opened_research_round_id,
                "cancelled_graph_run_count": result.cancelled_graph_run_count,
                "ordinary_experiment_cleanup_state": "gc_pending",
            },
        )

    @app.post(
        "/api/v2/workspace/graph-drafts/{graph_draft_id}/change-previews",
        response_model=GraphChangePreviewResponse,
        tags=["workspace-v022"],
    )
    def preview_graph_draft_change(
        graph_draft_id: uuid.UUID, request: GraphChangePreviewRequest
    ) -> GraphChangePreviewResponse:
        if graph_drafts is None:
            raise HTTPException(status_code=503, detail="Graph Draft commands unavailable")
        actor_key = authenticated_actor(request.actor_key)
        require_mutation("v022_research")
        preview = graph_drafts.preview_cascade_deselect(
            graph_draft_id,
            expected_revision=request.expected_revision,
            actor_key=actor_key,
            feature_key=request.feature_key,
            stage_no=request.stage_no,
        )
        return GraphChangePreviewResponse(
            context=_context(), quality=QualitySummary(state="warning"), **preview.to_dict()
        )

    @app.post(
        "/api/v2/workspace/graph-drafts/{graph_draft_id}/rebase-previews",
        response_model=GraphChangePreviewResponse,
        tags=["workspace-v022"],
    )
    def preview_graph_catalog_rebase(
        graph_draft_id: uuid.UUID, request: GraphCatalogRebasePreviewRequest
    ) -> GraphChangePreviewResponse:
        if graph_drafts is None:
            raise HTTPException(status_code=503, detail="Graph Draft commands unavailable")
        actor_key = authenticated_actor(request.actor_key)
        require_mutation("v022_research")
        preview = graph_drafts.preview_catalog_rebase(
            graph_draft_id,
            expected_revision=request.expected_revision,
            actor_key=actor_key,
        )
        return GraphChangePreviewResponse(
            context=_context(), quality=QualitySummary(state="warning"), **preview.to_dict()
        )

    @app.post(
        "/api/v2/workspace/graph-drafts/{graph_draft_id}/change-previews/{impact_token}/confirm",
        response_model=GraphDraftSnapshotResponse,
        tags=["workspace-v022"],
    )
    def confirm_graph_draft_change(
        graph_draft_id: uuid.UUID,
        impact_token: str,
        request: GraphChangeConfirmRequest,
    ) -> GraphDraftSnapshotResponse:
        if graph_drafts is None:
            raise HTTPException(status_code=503, detail="Graph Draft commands unavailable")
        actor_key = authenticated_actor(request.actor_key)
        require_mutation("v022_research")
        snapshot = graph_drafts.confirm_change_preview(
            graph_draft_id,
            impact_token,
            expected_revision=request.expected_revision,
            actor_key=actor_key,
            idempotency_key=request.idempotency_key,
        )
        return graph_draft_response(snapshot, applied=True)

    @app.post(
        "/api/v2/workspace/graph-drafts/{graph_draft_id}/compile",
        response_model=GraphDraftCompileResponse,
        tags=["workspace-v022"],
    )
    def compile_graph_draft(
        graph_draft_id: uuid.UUID, request: GraphDraftCompileRequest
    ) -> GraphDraftCompileResponse:
        if graph_drafts is None:
            raise HTTPException(status_code=503, detail="Graph Draft commands unavailable")
        actor_key = authenticated_actor(request.actor_key)
        replayed = graph_drafts.replay_compile(
            graph_draft_id,
            expected_revision=request.expected_revision,
            actor_key=actor_key,
            idempotency_key=request.idempotency_key,
        )
        if replayed is not None:
            return GraphDraftCompileResponse(
                context=_context(), quality=QualitySummary(state="ok"), **replayed.to_dict()
            )
        require_mutation("v022_research")
        outcome = graph_drafts.compile(
            graph_draft_id,
            expected_revision=request.expected_revision,
            actor_key=actor_key,
            idempotency_key=request.idempotency_key,
        )
        return GraphDraftCompileResponse(
            context=_context(), quality=QualitySummary(state="ok"), **outcome.to_dict()
        )

    @app.post(
        "/api/v2/workspace/graph-suite-launch-batches",
        response_model=GraphSuiteLaunchBatchResponse,
        tags=["workspace-v022", "experiment-v022"],
    )
    def submit_graph_suite_launch_batch(
        request: GraphSuiteLaunchBatchRequest,
    ) -> GraphSuiteLaunchBatchResponse:
        actor_key = authenticated_actor(request.actor_key)
        if graph_suite_batches is None:
            raise HTTPException(
                status_code=503,
                detail="v0.22 dual-frequency Suite launch is not enabled",
            )
        if enforce_suite_worker_readiness:
            readiness = runtime_heartbeat.read()
            if not readiness.ready:
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "v0.22 backtest runtime is not ready "
                        f"(suite_worker.{readiness.state})"
                    ),
                )
        require_mutation("v022_research")
        submitted = graph_suite_batches.submit(
            SuiteLaunchBatchRequest(
                actor_key=actor_key,
                idempotency_key=request.idempotency_key,
                source_graph_draft_id=request.source_graph_draft_id,
                source_graph_draft_revision=request.source_graph_draft_revision,
                source_compiled_research_graph_id=(
                    request.source_compiled_research_graph_id
                ),
                frequencies=tuple(request.frequencies),
                suite_mode=request.suite_mode,
            )
        )
        return GraphSuiteLaunchBatchResponse(
            context=_context(), quality=QualitySummary(state="ok"), **submitted
        )

    @app.get(
        "/api/v2/workspace/graph-suite-launch-batches/{suite_launch_batch_id}",
        response_model=GraphSuiteLaunchBatchResponse,
        tags=["workspace-v022", "experiment-v022"],
    )
    def graph_suite_launch_batch_status(
        suite_launch_batch_id: uuid.UUID,
    ) -> GraphSuiteLaunchBatchResponse:
        if graph_suite_batches is None:
            raise HTTPException(
                status_code=503,
                detail="v0.22 dual-frequency Suite launch is not enabled",
            )
        status = graph_suite_batches.status(suite_launch_batch_id)
        return GraphSuiteLaunchBatchResponse(
            context=_context(), quality=QualitySummary(state="ok"), **status
        )

    @app.post(
        "/api/v2/workspace/graph-suites",
        response_model=GraphSuiteSubmitResponse,
        tags=["workspace-v022", "experiment-v022"],
    )
    def submit_graph_suite(request: GraphSuiteSubmitRequest) -> GraphSuiteSubmitResponse:
        actor_key = authenticated_actor(request.actor_key)
        if graph_suites is None:
            raise HTTPException(
                status_code=503,
                detail="v0.22 Strategy/Defense/Portfolio Suite runtime is not enabled",
            )
        replayed = graph_suites.replay(
            actor_key=actor_key,
            idempotency_key=request.idempotency_key,
            compiled_research_graph_id=request.compiled_research_graph_id,
            suite_mode=request.suite_mode,
        )
        if replayed is not None:
            if graph_drafts is None:
                raise HTTPException(status_code=503, detail="Graph Draft commands unavailable")
            graph_drafts.lock_for_experiment(
                request.graph_draft_id,
                expected_revision=request.graph_draft_revision,
                actor_key=actor_key,
                compiled_research_graph_id=request.compiled_research_graph_id,
            )
            return GraphSuiteSubmitResponse(
                context=_context(), quality=QualitySummary(state="ok"), **replayed
            )
        if enforce_suite_worker_readiness:
            readiness = runtime_heartbeat.read()
            if not readiness.ready:
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "v0.22 backtest runtime is not ready "
                        f"(suite_worker.{readiness.state})"
                    ),
                )
        require_mutation("v022_research")
        if graph_drafts is None:
            raise HTTPException(status_code=503, detail="Graph Draft commands unavailable")
        submitted = graph_suites.submit(
            actor_key=actor_key,
            idempotency_key=request.idempotency_key,
            compiled_research_graph_id=request.compiled_research_graph_id,
            suite_mode=request.suite_mode,
        )
        graph_drafts.lock_for_experiment(
            request.graph_draft_id,
            expected_revision=request.graph_draft_revision,
            actor_key=actor_key,
            compiled_research_graph_id=request.compiled_research_graph_id,
        )
        return GraphSuiteSubmitResponse(
            context=_context(), quality=QualitySummary(state="ok"), **submitted
        )

    @app.get(
        "/api/v2/workspace/graph-suites",
        response_model=GraphSuiteListResponse,
        tags=["workspace-v022", "experiment-v022"],
    )
    def graph_suite_list(
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> GraphSuiteListResponse:
        if graph_suites is None:
            raise HTTPException(
                status_code=503,
                detail="v0.22 Strategy/Defense/Portfolio Suite runtime is not enabled",
            )
        return GraphSuiteListResponse(
            context=_context(), quality=QualitySummary(state="ok"),
            **graph_suites.list_suites(limit=limit, offset=offset),
        )

    @app.get(
        "/api/v2/workspace/graph-suite-runtime/readiness",
        response_model=GraphSuiteRuntimeReadinessResponse,
        tags=["workspace-v022", "experiment-v022"],
    )
    def graph_suite_runtime_readiness() -> GraphSuiteRuntimeReadinessResponse:
        readiness = runtime_heartbeat.read()
        return GraphSuiteRuntimeReadinessResponse(
            context=_context(),
            quality=QualitySummary(
                state="ok" if readiness.ready else "warning",
                codes=[] if readiness.ready else [f"suite_worker.{readiness.state}"],
            ),
            ready=readiness.ready,
            state=readiness.state,
            worker_key=readiness.worker_key,
            process_id=readiness.process_id,
            heartbeat_at=readiness.heartbeat_at,
            max_age_seconds=readiness.max_age_seconds,
            error_summary=readiness.error_summary,
        )

    @app.get(
        "/api/v2/workspace/graph-suites/{research_suite_id}",
        response_model=GraphSuiteStatusResponse,
        tags=["workspace-v022", "experiment-v022"],
    )
    def graph_suite_status(research_suite_id: uuid.UUID) -> GraphSuiteStatusResponse:
        if graph_suites is None:
            raise HTTPException(
                status_code=503,
                detail="v0.22 Strategy/Defense/Portfolio Suite runtime is not enabled",
            )
        return GraphSuiteStatusResponse(
            context=_context(), quality=QualitySummary(state="ok"),
            **graph_suites.status(research_suite_id),
        )

    @app.get(
        "/api/v2/workspace/graph-suites/{research_suite_id}/results",
        response_model=GraphSuiteResultsResponse,
        tags=["workspace-v022", "experiment-v022"],
    )
    def graph_suite_results(research_suite_id: uuid.UUID) -> GraphSuiteResultsResponse:
        if graph_suites is None:
            raise HTTPException(
                status_code=503,
                detail="v0.22 Strategy/Defense/Portfolio Suite runtime is not enabled",
            )
        return GraphSuiteResultsResponse(
            context=_context(), quality=QualitySummary(state="ok"),
            **graph_suites.results(research_suite_id),
        )

    @app.get(
        "/api/v2/workspace/drafts/{researcher_id}/{draft_key}",
        response_model=WorkspaceDraftResponse,
        tags=["workspace"],
    )
    def workspace_draft(researcher_id: str, draft_key: str) -> WorkspaceDraftResponse:
        if commands is None:
            raise HTTPException(status_code=503, detail="Workspace commands unavailable")
        return WorkspaceDraftResponse(
            context=_context(),
            quality=QualitySummary(state="ok"),
            **commands.get_workspace_draft(researcher_id=researcher_id, draft_key=draft_key),
        )

    @app.put(
        "/api/v2/workspace/drafts/{researcher_id}/{draft_key}",
        response_model=WorkspaceDraftResponse,
        tags=["workspace"],
    )
    def save_workspace_draft(
        researcher_id: str, draft_key: str, request: WorkspaceDraftSaveRequest
    ) -> WorkspaceDraftResponse:
        if researcher_id != request.researcher_id or draft_key != request.draft_key:
            raise HTTPException(status_code=422, detail="Draft path and body identity differ")
        if commands is None:
            raise HTTPException(status_code=503, detail="Workspace commands unavailable")
        actor_key = authenticated_actor(researcher_id)
        return WorkspaceDraftResponse(
            context=_context(),
            quality=QualitySummary(state="ok"),
            **commands.idempotent(
                command_name="save_workspace_draft",
                idempotency_key=request.idempotency_key,
                request=request.model_dump(mode="json"),
                operation=lambda: admitted_operation(
                    "v021_research",
                    lambda: commands.save_workspace_draft(
                        researcher_id=actor_key,
                        draft_key=draft_key,
                        name=request.name,
                        selection=request.selection.model_dump(mode="json"),
                        expected_revision=request.expected_revision,
                    ),
                ),
            ),
        )

    @app.post(
        "/api/v2/workspace/suites",
        response_model=WorkspaceSuiteSubmitResponse,
        tags=["workspace"],
    )
    def submit_workspace_suite(
        request: WorkspaceSuiteSubmitRequest,
    ) -> WorkspaceSuiteSubmitResponse:
        if commands is None:
            raise HTTPException(status_code=503, detail="Workspace commands unavailable")
        actor_key = authenticated_actor(request.researcher_id)
        payload = WorkspaceSuiteSubmitResponse(
            context=_context(),
            quality=QualitySummary(state="ok"),
            **commands.idempotent(
                command_name="submit_workspace_suite",
                idempotency_key=request.idempotency_key,
                request=request.model_dump(mode="json"),
                operation=lambda: admitted_operation(
                    "v021_research",
                    lambda: commands.submit_workspace_draft(
                        researcher_id=actor_key,
                        draft_key=request.draft_key,
                        expected_revision=request.expected_revision,
                        suite_mode=request.suite_mode,
                    ),
                ),
            ),
        )
        return payload

    @app.get(
        "/api/v2/workspace/suites/{research_suite_id}",
        response_model=WorkspaceSuiteStatusResponse,
        tags=["workspace"],
    )
    def workspace_suite_status(
        research_suite_id: uuid.UUID,
    ) -> WorkspaceSuiteStatusResponse:
        if commands is None:
            raise HTTPException(status_code=503, detail="Workspace commands unavailable")
        return WorkspaceSuiteStatusResponse(
            context=_context(),
            quality=QualitySummary(state="ok"),
            **commands.suite_status(research_suite_id),
        )

    @app.post(
        "/api/v2/workspace/suites/{research_suite_id}/cancel",
        response_model=WorkspaceSuiteCancelResponse,
        tags=["workspace"],
    )
    def cancel_workspace_suite(
        research_suite_id: uuid.UUID,
        request: CommandIdempotencyRequest,
    ) -> WorkspaceSuiteCancelResponse:
        if commands is None:
            raise HTTPException(status_code=503, detail="Workspace commands unavailable")
        return WorkspaceSuiteCancelResponse(
            context=_context(),
            quality=QualitySummary(state="warning"),
            **commands.idempotent(
                command_name="cancel_workspace_suite",
                idempotency_key=request.idempotency_key,
                request={"research_suite_id": str(research_suite_id)},
                operation=lambda: admitted_operation(
                    "suite_cancellation",
                    lambda: commands.cancel_suite(research_suite_id),
                ),
            ),
        )

    @app.get(
        "/api/v2/release-gates",
        response_model=ReleaseGateResponse,
        tags=["system"],
    )
    def release_gates() -> ReleaseGateResponse:
        gate_payload = (
            commands.release_gates() if commands is not None else current_release_gates().to_dict()
        )
        return ReleaseGateResponse(
            context=_context(),
            quality=QualitySummary(state="warning"),
            formal_enabled=bool(gate_payload["formal_enabled"]),
            product_enabled=bool(gate_payload["product_enabled"]),
            reason_codes=[str(item) for item in cast(list[object], gate_payload["reason_codes"])],
        )

    @app.get(
        "/api/v2/products",
        response_model=ProductCatalogResponse,
        tags=["product"],
    )
    def products() -> ProductCatalogResponse:
        return ProductCatalogResponse(
            context=_context(),
            quality=QualitySummary(state="ok"),
            **reader.product_catalog(),
        )

    @app.get(
        "/api/v2/products/{enrollment_id}",
        response_model=ProductDetailResponse,
        tags=["product"],
    )
    def product_detail(enrollment_id: uuid.UUID) -> ProductDetailResponse:
        return ProductDetailResponse(
            context=_context(),
            quality=QualitySummary(state="ok"),
            **reader.product_detail(enrollment_id),
        )

    @app.get(
        "/api/v2/v022/products",
        response_model=V022ProductIdentityCatalogResponse,
        tags=["product", "v0.22"],
    )
    def v022_product_identities() -> V022ProductIdentityCatalogResponse:
        return V022ProductIdentityCatalogResponse(
            context=_context(),
            quality=QualitySummary(state="ok"),
            **reader.v022_product_identity_catalog(),
        )

    @app.get(
        "/api/v2/v022/products/{enrollment_id}",
        response_model=V022ProductIdentityDetailResponse,
        tags=["product", "v0.22"],
    )
    def v022_product_identity(
        enrollment_id: uuid.UUID,
    ) -> V022ProductIdentityDetailResponse:
        return V022ProductIdentityDetailResponse(
            context=_context(),
            quality=QualitySummary(state="ok"),
            **reader.v022_product_identity_detail(enrollment_id),
        )

    @app.post(
        "/api/v2/v022/products/{enrollment_id}/lifecycle",
        response_model=V022ProductLifecycleResponse,
        tags=["product", "v0.22"],
    )
    def change_v022_product_lifecycle(
        enrollment_id: uuid.UUID,
        request: V022ProductLifecycleRequest,
    ) -> V022ProductLifecycleResponse:
        if v022_product_lifecycle is None or v022_command_idempotency is None:
            raise HTTPException(status_code=503, detail="v0.22 Product lifecycle unavailable")
        actor_key = require_actor_claim(
            actor_context, request.researcher_id, required_role="operator"
        )

        def operation() -> dict[str, Any]:
            require_mutation("product_operations")
            publication = v022_product_lifecycle.publish(
                product_enrollment_id=enrollment_id,
                expected_sequence=request.expected_sequence,
                target=request.target,
                reason_code=request.reason_code,
                reason=request.reason,
                requested_by=actor_key,
                requested_at=request.requested_at,
                effective_at=request.effective_at,
            )
            return {
                "product_enrollment_id": enrollment_id,
                "enrollment_lifecycle_event_id": publication.enrollment_lifecycle_event_id,
                "artifact_id": publication.artifact_id,
                "sequence_number": publication.sequence_number,
                "from_lifecycle": publication.from_lifecycle,
                "to_lifecycle": publication.to_lifecycle,
                "event_fingerprint": publication.event_fingerprint,
                "reused": publication.reused,
            }

        payload = v022_command_idempotency.execute(
            command_name="change_v022_product_lifecycle",
            idempotency_key=request.idempotency_key,
            request={
                "product_enrollment_id": str(enrollment_id),
                "actor_key": actor_key,
                **request.model_dump(mode="json", exclude={"idempotency_key", "researcher_id"}),
            },
            operation=operation,
        )
        return V022ProductLifecycleResponse(
            context=_context(),
            quality=QualitySummary(state="ok"),
            **payload,
        )

    @app.get(
        "/api/v2/products/{enrollment_id}/recommendation",
        response_model=ProductRecommendationResponse,
        tags=["product"],
    )
    def product_recommendation(enrollment_id: uuid.UUID) -> ProductRecommendationResponse:
        if product_recommendations is None:
            raise HTTPException(status_code=503, detail="Product recommendations unavailable")
        payload = product_recommendations.latest(enrollment_id)
        return ProductRecommendationResponse(
            context=_context(),
            quality=QualitySummary(state="ok" if payload["available"] else "warning"),
            **payload,
        )

    @app.post(
        "/api/v2/products/{enrollment_id}/lifecycle",
        response_model=ProductLifecycleChangeResponse,
        tags=["product"],
    )
    def change_product_lifecycle(
        enrollment_id: uuid.UUID,
        request: ProductLifecycleChangeRequest,
    ) -> ProductLifecycleChangeResponse:
        if commands is None:
            raise HTTPException(status_code=503, detail="Product commands unavailable")
        actor_key = authenticated_actor(request.researcher_id)
        return ProductLifecycleChangeResponse(
            context=_context(),
            quality=QualitySummary(state="ok"),
            **commands.idempotent(
                command_name="change_product_lifecycle",
                idempotency_key=request.idempotency_key,
                request={"enrollment_id": str(enrollment_id), **request.model_dump(mode="json")},
                operation=lambda: admitted_operation(
                    "product_operations",
                    lambda: commands.change_product_lifecycle(
                        enrollment_id,
                        target=request.target,
                        expected_revision=request.expected_revision,
                        reason_code=request.reason_code,
                        reason=request.reason,
                        researcher_id=actor_key,
                        requested_at=request.requested_at,
                        effective_at=request.effective_at,
                    ),
                ),
            ),
        )

    @app.post(
        "/api/v2/products/alerts/{alert_id}/status",
        response_model=ProductAlertChangeResponse,
        tags=["product"],
    )
    def change_product_alert(
        alert_id: uuid.UUID,
        request: ProductAlertChangeRequest,
    ) -> ProductAlertChangeResponse:
        if commands is None:
            raise HTTPException(status_code=503, detail="Product commands unavailable")
        actor_key = authenticated_actor(request.researcher_id)
        return ProductAlertChangeResponse(
            context=_context(),
            quality=QualitySummary(state="ok"),
            **commands.idempotent(
                command_name="change_product_alert",
                idempotency_key=request.idempotency_key,
                request={"alert_id": str(alert_id), **request.model_dump(mode="json")},
                operation=lambda: admitted_operation(
                    "product_operations",
                    lambda: commands.change_product_alert(
                        alert_id,
                        target=request.target,
                        researcher_id=actor_key,
                        note=request.note,
                        occurred_at=request.occurred_at,
                    ),
                ),
            ),
        )

    @app.post(
        "/api/v2/products/{enrollment_id}/reviews",
        response_model=ProductReviewResponse,
        tags=["product"],
    )
    def record_product_review(
        enrollment_id: uuid.UUID, request: ProductReviewRequest
    ) -> ProductReviewResponse:
        if commands is None:
            raise HTTPException(status_code=503, detail="Product commands unavailable")
        actor_key = authenticated_actor(request.researcher_id)
        return ProductReviewResponse(
            context=_context(),
            quality=QualitySummary(state="ok"),
            **commands.idempotent(
                command_name="record_product_review",
                idempotency_key=request.idempotency_key,
                request={"enrollment_id": str(enrollment_id), **request.model_dump(mode="json")},
                operation=lambda: admitted_operation(
                    "product_operations",
                    lambda: commands.record_product_review(
                        enrollment_id,
                        decision=request.decision,
                        researcher_id=actor_key,
                        reason=request.reason,
                        evidence=request.evidence,
                        reviewed_at=request.reviewed_at,
                    ),
                ),
            ),
        )

    @app.get("/api/v2/factors/datasets/{artifact_id}/download.csv", tags=["factor"])
    def factor_download(artifact_id: uuid.UUID) -> Response:
        dataset = reader.factor_values(artifact_id)
        output = io.StringIO(newline="")
        writer = csv.DictWriter(
            output, fieldnames=["observation_date", "asset_key", "symbol", "value"]
        )
        writer.writeheader()
        writer.writerows(dataset["rows"])
        filename = f"{dataset['variant_key']}.csv"
        return Response(
            content=output.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "X-Artifact-Content-Hash": str(dataset["content_hash"]),
            },
        )

    @app.get("/api/v2/signals/versions/{version_key}/download.csv", tags=["signal"])
    def signal_download(version_key: str) -> Response:
        dataset = reader.signal_values(version_key)
        output = io.StringIO(newline="")
        writer = csv.DictWriter(
            output,
            fieldnames=[
                "observation_date",
                "asset_key",
                "symbol",
                "score",
                "state",
                "event",
            ],
        )
        writer.writeheader()
        writer.writerows(dataset["rows"])
        return Response(
            content=output.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{version_key}.csv"',
                "X-Artifact-Content-Hash": str(dataset["content_hash"]),
            },
        )

    def asset_export_preview(
        request: AssetDataExportPreviewRequest,
    ) -> tuple[str, AssetDataExportPreview]:
        if asset_data_exports is None:
            raise HTTPException(status_code=503, detail="Asset Data Export unavailable")
        actor_key = authenticated_actor(request.researcher_key)
        try:
            preview = asset_data_exports.preview(
                researcher_key=actor_key,
                graph_draft_id=request.graph_draft_id,
                graph_draft_revision=request.graph_draft_revision,
                export_format=request.export_format,
                start_date=request.start_date,
                end_date=request.end_date,
                fields=request.fields,
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return actor_key, preview

    @app.post(
        "/api/v2/v022/asset-data-exports/preview",
        response_model=AssetDataExportPreviewResponse,
        tags=["workspace-v022"],
    )
    def preview_asset_data_export(
        request: AssetDataExportPreviewRequest,
    ) -> AssetDataExportPreviewResponse:
        _actor_key, preview = asset_export_preview(request)
        return _asset_data_export_preview_response(preview)

    @app.post(
        "/api/v2/v022/asset-data-exports",
        response_model=AssetDataExportJobResponse,
        status_code=202,
        tags=["workspace-v022"],
    )
    def create_asset_data_export(
        request: AssetDataExportPreviewRequest,
    ) -> AssetDataExportJobResponse:
        if asset_data_exports is None:
            raise HTTPException(status_code=503, detail="Asset Data Export unavailable")
        require_mutation("historical_export")
        _actor_key, preview = asset_export_preview(request)
        try:
            job = asset_data_exports.enqueue(preview)
        except RuntimeError as error:
            status_code = 507 if str(error) == "asset_export_insufficient_disk_space" else 409
            raise HTTPException(status_code=status_code, detail=str(error)) from error
        return _asset_data_export_job_response(job)

    @app.get(
        "/api/v2/v022/asset-data-exports/{export_job_id}",
        response_model=AssetDataExportJobResponse,
        tags=["workspace-v022"],
    )
    def asset_data_export_status(export_job_id: uuid.UUID) -> AssetDataExportJobResponse:
        if asset_data_exports is None or actor_context is None:
            raise HTTPException(status_code=503, detail="Asset Data Export unavailable")
        actor_key = require_actor_role(actor_context, required_role="researcher")
        return _asset_data_export_job_response(
            asset_data_exports.get(export_job_id, researcher_key=actor_key)
        )

    @app.post(
        "/api/v2/v022/asset-data-exports/{export_job_id}/cancel",
        response_model=AssetDataExportJobResponse,
        tags=["workspace-v022"],
    )
    def cancel_asset_data_export(export_job_id: uuid.UUID) -> AssetDataExportJobResponse:
        if asset_data_exports is None or actor_context is None:
            raise HTTPException(status_code=503, detail="Asset Data Export unavailable")
        require_mutation("historical_export")
        actor_key = require_actor_role(actor_context, required_role="researcher")
        return _asset_data_export_job_response(
            asset_data_exports.cancel(export_job_id, researcher_key=actor_key)
        )

    @app.get(
        "/api/v2/v022/asset-data-exports/{export_job_id}/download",
        response_class=FileResponse,
        tags=["workspace-v022"],
    )
    def asset_data_export_download(export_job_id: uuid.UUID) -> FileResponse:
        if asset_data_exports is None or actor_context is None:
            raise HTTPException(status_code=503, detail="Asset Data Export unavailable")
        actor_key = require_actor_role(actor_context, required_role="researcher")
        artifact = asset_data_exports.validated_download(
            export_job_id,
            researcher_key=actor_key,
        )
        return FileResponse(
            path=artifact.path,
            media_type="application/zip",
            filename=artifact.filename,
            headers={
                "X-Artifact-Content-Hash": artifact.content_hash,
                "Cache-Control": "private, no-store",
            },
        )

    @app.post(
        "/api/v2/signals/research-export.zip",
        response_model=SignalResearchExportJobResponse,
        status_code=202,
        tags=["signal"],
    )
    def signal_research_export(
        request: SignalResearchExportRequest,
    ) -> SignalResearchExportJobResponse:
        if signal_export_jobs is None:
            raise HTTPException(status_code=503, detail="Signal export service unavailable")
        require_mutation("historical_export")
        unsupported_inputs = sorted(
            {
                input_key
                for input_keys in request.asset_data_inputs.values()
                for input_key in input_keys
                if input_key != "canonical_market_bars"
            }
        )
        missing_market_inputs = sorted(
            str(security_id)
            for security_id in request.asset_security_ids
            if "canonical_market_bars" not in request.asset_data_inputs[security_id]
        )
        if unsupported_inputs or missing_market_inputs:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "signal_export_asset_input_invalid",
                    "unsupported_input_keys": unsupported_inputs,
                    "missing_canonical_market_bars": missing_market_inputs,
                },
            )
        input_options = reader.workspace_options(
            frequency=request.frequency,
            selected_factor_variants=(),
            selected_signals=(),
            selected_assets=tuple(request.asset_security_ids),
            selected_asset_data_inputs={
                str(security_id): tuple(input_keys)
                for security_id, input_keys in request.asset_data_inputs.items()
            },
        )
        if input_options["asset_data_input_blockers"] or input_options["usable_asset_count"] != len(
            request.asset_security_ids
        ):
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "signal_export_asset_input_unavailable",
                    "asset_data_input_blockers": input_options["asset_data_input_blockers"],
                    "selected_asset_count": len(request.asset_security_ids),
                    "usable_asset_count": input_options["usable_asset_count"],
                },
            )
        job = signal_export_jobs.enqueue(request.model_dump(mode="json"))
        return _signal_export_job_response(job)

    @app.get(
        "/api/v2/signals/research-exports/{export_job_id}",
        response_model=SignalResearchExportJobResponse,
        tags=["signal"],
    )
    def signal_research_export_status(
        export_job_id: uuid.UUID,
    ) -> SignalResearchExportJobResponse:
        if signal_export_jobs is None:
            raise HTTPException(status_code=503, detail="Signal export service unavailable")
        return _signal_export_job_response(signal_export_jobs.get(export_job_id))

    @app.get(
        "/api/v2/signals/research-exports/{export_job_id}/download",
        response_class=FileResponse,
        tags=["signal"],
    )
    def signal_research_export_download(export_job_id: uuid.UUID) -> FileResponse:
        if signal_export_jobs is None:
            raise HTTPException(status_code=503, detail="Signal export service unavailable")
        job = signal_export_jobs.get(export_job_id)
        if job.status != "completed":
            raise HTTPException(
                status_code=409,
                detail={"code": "signal_export_not_ready", "status": job.status},
            )
        artifact = signal_export_jobs.validated_download(export_job_id)
        return FileResponse(
            path=artifact.path,
            media_type="application/zip",
            filename=artifact.filename,
            headers={
                "X-Artifact-Content-Hash": artifact.content_hash,
                "Cache-Control": "private, no-store",
            },
        )

    @app.get(
        "/api/v2/catalog/data-requirements",
        response_model=DataRequirementResponse,
        responses={304: {"description": "Not modified"}},
        tags=["catalog"],
    )
    def data_requirements(
        response: Response,
        if_none_match: Annotated[str | None, Header()] = None,
    ) -> DataRequirementResponse | Response:
        payload = DataRequirementResponse(
            context=_context(), quality=QualitySummary(state="ok"), **reader.data_requirements()
        )
        cached = _etag_response(payload, response, if_none_match)
        return cached or payload

    @app.get(
        "/api/v2/artifacts/{artifact_id}",
        response_model=ArtifactDetailResponse,
        responses={304: {"description": "Not modified"}},
        tags=["lineage"],
    )
    def artifact_detail(
        artifact_id: uuid.UUID,
        response: Response,
        if_none_match: Annotated[str | None, Header()] = None,
    ) -> ArtifactDetailResponse | Response:
        detail = reader.artifact_detail(artifact_id)
        artifact = _artifact(detail["artifact"])
        payload = ArtifactDetailResponse(
            context=_context(),
            quality=artifact.quality,
            artifact=artifact,
            direct_dependencies=[
                DependencySummary(**item) for item in detail["direct_dependencies"]
            ],
            direct_dependents=[DependencySummary(**item) for item in detail["direct_dependents"]],
            lineage_url=(
                f"/api/v2/artifacts/{artifact_id}/lineage" if detail["has_manifest"] else None
            ),
        )
        cached = _etag_response(payload, response, if_none_match)
        return cached or payload

    @app.get(
        "/api/v2/artifacts/{artifact_id}/lineage",
        response_model=LineageManifestResponse,
        responses={304: {"description": "Not modified"}},
        tags=["lineage"],
    )
    def lineage(
        artifact_id: uuid.UUID,
        response: Response,
        if_none_match: Annotated[str | None, Header()] = None,
    ) -> LineageManifestResponse | Response:
        result = reader.lineage_manifest(artifact_id)
        artifact = _artifact(result["artifact"])
        manifest = result["manifest"]
        if not isinstance(manifest, dict):
            raise RuntimeError("Stored lineage manifest must be an object")
        payload = LineageManifestResponse(
            context=_context(),
            quality=artifact.quality,
            root_artifact=artifact,
            manifest_hash=str(result["manifest_hash"]),
            canonical_version=str(result["canonical_version"]),
            artifacts=manifest["artifacts"],
            dependencies=[DependencySummary(**item) for item in manifest["dependencies"]],
            created_at=result["created_at"],
        )
        cached = _etag_response(payload, response, if_none_match)
        return cached or payload

    if static_dir.is_dir():
        assets_dir = static_dir / "assets"
        if assets_dir.is_dir():
            app.mount("/assets", StaticFiles(directory=assets_dir), name="frontend-assets")

        @app.get("/favicon.ico", include_in_schema=False)
        def favicon() -> Response:
            return Response(status_code=204)

        @app.get("/{frontend_path:path}", include_in_schema=False)
        def frontend(frontend_path: str) -> FileResponse:
            if frontend_path.startswith("api/"):
                raise HTTPException(status_code=404, detail="API route not found")
            return FileResponse(static_dir / "index.html")

    return app


app = create_app()
