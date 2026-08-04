from __future__ import annotations

import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, Any, Literal, Protocol

from fastapi import FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from style_rotation import __version__
from style_rotation.api.query import ArtifactQueryService
from style_rotation.api.schemas import (
    ApiContext,
    ApiError,
    ArtifactDetailResponse,
    ArtifactListResponse,
    ArtifactSummary,
    AssetCatalogResponse,
    CapabilitiesResponse,
    DataOverviewResponse,
    DataRequirementResponse,
    DatasetPublicationItem,
    DependencySummary,
    DomainCapability,
    ExperimentOverviewResponse,
    ExperimentResultResponse,
    FactorDatasetDiagnosticItem,
    FactorOverviewResponse,
    HealthResponse,
    LineageManifestResponse,
    ModelDiagnosticItem,
    ModelOverviewResponse,
    QualityState,
    QualitySummary,
    SignalDiagnosticItem,
    SignalOverviewResponse,
    StrategyOverviewResponse,
    StrategyTargetPathResponse,
)
from style_rotation.architecture import DOMAIN_BOUNDARIES
from style_rotation.config.settings import get_settings
from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.persistence.session import create_postgres_engine

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
    def asset_catalog(self) -> dict[str, Any]: ...
    def data_requirements(self) -> dict[str, Any]: ...
    def data_overview(self) -> dict[str, Any]: ...
    def factor_overview(self) -> dict[str, Any]: ...
    def signal_overview(self, frequency: str) -> dict[str, Any]: ...
    def model_overview(self, frequency: str) -> dict[str, Any]: ...
    def strategy_overview(self) -> dict[str, Any]: ...
    def strategy_target_path(self, artifact_id: uuid.UUID) -> dict[str, Any]: ...
    def experiment_overview(self) -> dict[str, Any]: ...
    def experiment_result(self, artifact_id: uuid.UUID) -> dict[str, Any]: ...


def _context() -> ApiContext:
    return ApiContext(system_version=__version__)


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


def create_app(
    reader: ArtifactReader | None = None,
    *,
    static_directory: Path | None = None,
) -> FastAPI:
    if reader is None:
        reader = ArtifactQueryService(create_postgres_engine(get_settings().database_url))
    static_dir = static_directory or DEFAULT_STATIC_DIR
    app = FastAPI(
        title="Style Rotation Research API",
        version=__version__,
        description="Read-only v0.2 research identity, quality, and lineage interface.",
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

    @app.get("/api/v2/health", response_model=HealthResponse, tags=["system"])
    def health() -> HealthResponse:
        return HealthResponse(
            context=_context(),
            quality=QualitySummary(state="ok"),
            database_revision=reader.database_revision(),
        )

    @app.get("/api/v2/capabilities", response_model=CapabilitiesResponse, tags=["system"])
    def capabilities() -> CapabilitiesResponse:
        available = {
            "catalog", "data", "factor", "signal", "model", "strategy", "experiment",
            "lineage", "ops",
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
        if_none_match: Annotated[str | None, Header()] = None,
    ) -> ExperimentOverviewResponse | Response:
        result = reader.experiment_overview()
        codes: list[str] = []
        if any(item["status"] == "failed" for item in result["specifications"]):
            codes.append("experiment.failed_cells")
        if any(
            item["availability_status"] == "excluded"
            for item in result["specifications"]
        ):
            codes.append("experiment.excluded_cells")
        payload = ExperimentOverviewResponse.model_validate({
            "context": _context(),
            "quality": QualitySummary(state="warning" if codes else "ok", codes=codes),
            **result,
        })
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
        payload = ExperimentResultResponse.model_validate({
            "context": _context(), "quality": QualitySummary(state="ok"),
            **reader.experiment_result(artifact_id),
        })
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
        if_none_match: Annotated[str | None, Header()] = None,
    ) -> AssetCatalogResponse | Response:
        payload = AssetCatalogResponse(
            context=_context(), quality=QualitySummary(state="ok"), **reader.asset_catalog()
        )
        cached = _etag_response(payload, response, if_none_match)
        return cached or payload

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
