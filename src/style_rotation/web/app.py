from __future__ import annotations

import uuid
from collections.abc import Sequence
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Annotated, Any, Protocol

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from style_rotation.config.settings import get_settings
from style_rotation.persistence.session import create_postgres_engine, create_session_factory
from style_rotation.web.repository import ResearchRepository


class ResearchReader(Protocol):
    def options(self) -> dict[str, Any]: ...
    def status(self) -> dict[str, Any]: ...
    def leaderboard(
        self,
        *,
        frequency: str,
        strategy_template: str,
        cost_bps: Decimal,
        sort_metric: str,
        descending: bool,
    ) -> dict[str, Any]: ...
    def factor_detail(self, factor_variant_id: uuid.UUID) -> dict[str, Any]: ...
    def compare(self, run_ids: Sequence[uuid.UUID], *, max_points: int) -> dict[str, Any]: ...


STATIC_DIR = Path(__file__).with_name("static")


def create_app(repository: ResearchReader | None = None) -> FastAPI:
    if repository is None:
        settings = get_settings()
        engine = create_postgres_engine(settings.database_url)
        repository = ResearchRepository(create_session_factory(engine))

    app = FastAPI(
        title="US Style Factor Research",
        version="0.1.0",
        description="Read-only interface for formal deterministic backtest results.",
    )
    app.state.repository = repository
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/api/v1/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "mode": "read_only"}

    @app.get("/api/v1/options")
    def options() -> dict[str, Any]:
        return _translate_lookup(repository.options)

    @app.get("/api/v1/status")
    def status() -> dict[str, Any]:
        return _translate_lookup(repository.status)

    @app.get("/api/v1/leaderboard")
    def leaderboard(
        frequency: str = Query("weekly", pattern="^(weekly|monthly)$"),
        strategy_template: str = Query(
            "cross_sectional", pattern="^(cross_sectional|trend_filtered)$"
        ),
        cost_bps: str = "5",
        sort_metric: str = "strategy.net.sharpe_ratio",
        descending: bool = True,
    ) -> dict[str, Any]:
        try:
            cost = Decimal(cost_bps)
        except InvalidOperation as error:
            raise HTTPException(status_code=422, detail="cost_bps must be numeric") from error
        return _translate_lookup(
            lambda: repository.leaderboard(
                frequency=frequency,
                strategy_template=strategy_template,
                cost_bps=cost,
                sort_metric=sort_metric,
                descending=descending,
            )
        )

    @app.get("/api/v1/factors/{factor_variant_id}")
    def factor_detail(factor_variant_id: uuid.UUID) -> dict[str, Any]:
        return _translate_lookup(lambda: repository.factor_detail(factor_variant_id))

    @app.get("/api/v1/compare")
    def compare(
        run_ids: Annotated[list[uuid.UUID], Query()],
        max_points: int = Query(600, ge=50, le=1500),
    ) -> dict[str, Any]:
        return _translate_lookup(lambda: repository.compare(run_ids, max_points=max_points))

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/factors/{factor_variant_id}", include_in_schema=False)
    def factor_page(factor_variant_id: uuid.UUID) -> FileResponse:
        return FileResponse(STATIC_DIR / "factor.html")

    @app.get("/compare", include_in_schema=False)
    def compare_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "compare.html")

    return app


def _translate_lookup(operation: Any) -> dict[str, Any]:
    try:
        result: dict[str, Any] = operation()
        return result
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


app = create_app()
