from __future__ import annotations

import argparse
import json
import sys
import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import NoReturn

from style_rotation import __version__
from style_rotation.architecture import DOMAIN_BOUNDARIES
from style_rotation.catalog.bootstrap import publish_catalogs
from style_rotation.catalog.eligibility import EligibilityPublicationService
from style_rotation.catalog.scope import publish_research_scope
from style_rotation.config.settings import get_settings
from style_rotation.data.acquisition import SourceAcquisitionService
from style_rotation.data.bundle import (
    ReservePublicationService,
    publish_data_bundle,
    publish_reserve_model,
)
from style_rotation.data.calendar import CalendarPublicationService, XNYSCalendarGenerator
from style_rotation.data.forward_return_engine import (
    build_forward_return_engine_spec,
    publish_forward_return_engine,
)
from style_rotation.data.forward_return_publication import (
    ForwardReturnDatasetPublicationService,
    publish_forward_return_catalog,
)
from style_rotation.data.providers.snapshots import (
    FredCsvSnapshotAdapter,
    YahooYFinanceSnapshotAdapter,
)
from style_rotation.data.publication import CanonicalDataPublicationService
from style_rotation.data.service import publish_data_contracts
from style_rotation.factor.diagnostic_publication import FactorDiagnosticPublicationService
from style_rotation.factor.engine import (
    build_factor_diagnostic_engine_spec,
    build_factor_engine_spec,
    publish_factor_diagnostic_engine,
    publish_factor_engine,
)
from style_rotation.factor.publication import FactorDatasetPublicationService
from style_rotation.factor.service import publish_factor_catalog
from style_rotation.lineage.service import ArtifactService
from style_rotation.model.diagnostic_publication import ModelDiagnosticPublicationService
from style_rotation.model.engine import (
    build_model_engine_spec,
    build_model_evaluation_engine_spec,
    publish_model_engine,
    publish_model_evaluation_engine,
)
from style_rotation.model.publication import ModelDatasetPublicationService
from style_rotation.model.service import publish_model_catalog
from style_rotation.persistence.database import database_status, reset_database, upgrade_database
from style_rotation.persistence.session import create_postgres_engine
from style_rotation.signal.diagnostic_engine import (
    build_signal_evaluation_engine_spec,
    publish_signal_evaluation_engine,
)
from style_rotation.signal.diagnostic_publication import SignalDiagnosticPublicationService
from style_rotation.signal.engine import build_signal_engine_spec, publish_signal_engine
from style_rotation.signal.publication import SignalDatasetPublicationService
from style_rotation.signal.service import publish_signal_catalog
from style_rotation.strategy.engine import (
    build_strategy_target_engine_spec,
    publish_strategy_target_engine,
)
from style_rotation.strategy.product_service import publish_strategy_product
from style_rotation.strategy.service import publish_strategy_catalog
from style_rotation.strategy.target_publication import StrategyTargetPublicationService


@dataclass(frozen=True, slots=True)
class PlannedCommand:
    key: str
    summary: str
    delivery_milestone: str


PLANNED_COMMANDS = (
    PlannedCommand("bootstrap", "Publish versioned research catalogs", "M1C"),
    PlannedCommand("data", "Ingest, validate, and publish market data", "M2"),
    PlannedCommand("factor", "Calculate and publish factor datasets", "M3"),
    PlannedCommand("signal", "Calculate and publish signal datasets", "M4"),
    PlannedCommand("model", "Calculate and publish model datasets", "M5"),
    PlannedCommand("strategy", "Generate strategy products and target paths", "M6"),
    PlannedCommand("experiment", "Plan and run versioned experiments", "M7"),
    PlannedCommand("lineage", "Inspect artifact dependencies and manifests", "M1C"),
    PlannedCommand("artifact", "Inspect publication identity and status", "M1C"),
    PlannedCommand("backup", "Create and verify database backups", "M9"),
    PlannedCommand("api", "Run the local read-only API and application", "M1D"),
)


def _planned(command: PlannedCommand) -> int:
    print(
        f"'{command.key}' is registered but not implemented; "
        f"delivery milestone: {command.delivery_milestone}.",
        file=sys.stderr,
    )
    return 2


def _show_modules(as_json: bool) -> int:
    if as_json:
        print(json.dumps([asdict(item) for item in DOMAIN_BOUNDARIES], indent=2))
        return 0
    for boundary in DOMAIN_BOUNDARIES:
        upstream = ",".join(boundary.upstream) if boundary.upstream else "-"
        print(
            f"{boundary.key:10} milestone={boundary.delivery_milestone:3} "
            f"upstream={upstream} purpose={boundary.purpose}"
        )
    return 0


def _db_status(as_json: bool) -> int:
    status = database_status(get_settings().database_url)
    if as_json:
        print(json.dumps(status.to_dict(), indent=2))
    else:
        revision = status.current_revision or "unversioned"
        print(f"database={status.database_name} revision={revision}")
        print(f"schemas={','.join(status.present_schemas) or '-'}")
        print(f"missing={','.join(status.missing_schemas) or '-'}")
    return 0


def _db_upgrade() -> int:
    settings = get_settings()
    upgrade_database(settings.database_url)
    return _db_status(as_json=False)


def _db_reset(confirmation: str) -> int:
    settings = get_settings()
    reset_database(settings.database_url, confirmation, settings.environment)
    return _db_status(as_json=False)


def _artifact_service() -> ArtifactService:
    return ArtifactService(create_postgres_engine(get_settings().database_url))


def _bootstrap_catalogs(catalog_directory: str) -> int:
    results = publish_catalogs(_artifact_service(), Path(catalog_directory))
    print(json.dumps(results, indent=2, ensure_ascii=False))
    return 0


def _bootstrap_scope(catalog_file: str) -> int:
    engine = create_postgres_engine(get_settings().database_url)
    results = publish_research_scope(engine, Path(catalog_file))
    print(json.dumps(results, indent=2, ensure_ascii=False))
    return 0


def _bootstrap_data_contracts(catalog_file: str) -> int:
    engine = create_postgres_engine(get_settings().database_url)
    results = publish_data_contracts(engine, Path(catalog_file))
    print(json.dumps(results, indent=2, ensure_ascii=False))
    return 0


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("expected an ISO date (YYYY-MM-DD)") from error


def _data_fetch(
    start: date,
    end_inclusive: date,
    symbols: tuple[str, ...],
    skip_market: bool,
    skip_rate: bool,
) -> int:
    if skip_market and skip_rate:
        raise ValueError("Cannot skip both market and rate acquisition")
    settings = get_settings()
    engine = create_postgres_engine(settings.database_url)
    service = SourceAcquisitionService(
        engine,
        YahooYFinanceSnapshotAdapter(settings.yahoo_timeout_seconds),
        FredCsvSnapshotAdapter(settings.fred_csv_url, settings.fred_timeout_seconds),
    )
    results = service.acquire(
        symbols=symbols,
        start=start,
        end_inclusive=end_inclusive,
        include_market=not skip_market,
        include_rate=not skip_rate,
    )
    print(json.dumps([item.to_dict() for item in results], indent=2, ensure_ascii=False))
    return 0


def _data_calendar(start: date, end_inclusive: date, version_number: int) -> int:
    generated = XNYSCalendarGenerator().generate(start, end_inclusive)
    published = CalendarPublicationService(
        create_postgres_engine(get_settings().database_url)
    ).publish(generated, version_number=version_number)
    payload = asdict(published)
    payload["artifact_id"] = str(published.artifact_id)
    payload["session_count"] = len(generated.sessions)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def _canonical_market(
    snapshot_artifact_ids: tuple[str, ...], calendar_artifact_id: str, version_number: int
) -> int:
    service = CanonicalDataPublicationService(create_postgres_engine(get_settings().database_url))
    result = service.publish_market(
        tuple(uuid.UUID(item) for item in snapshot_artifact_ids),
        uuid.UUID(calendar_artifact_id),
        version_number=version_number,
    )
    payload = asdict(result)
    payload["artifact_id"] = str(result.artifact_id)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def _canonical_rate(snapshot_artifact_id: str, version_number: int) -> int:
    service = CanonicalDataPublicationService(create_postgres_engine(get_settings().database_url))
    result = service.publish_rate(uuid.UUID(snapshot_artifact_id), version_number=version_number)
    payload = asdict(result)
    payload["artifact_id"] = str(result.artifact_id)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def _bootstrap_reserve_model() -> int:
    results = publish_reserve_model(create_postgres_engine(get_settings().database_url))
    payload = []
    for result in results:
        item = asdict(result)
        item["artifact_id"] = str(result.artifact_id)
        payload.append(item)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def _factor_bootstrap(catalog_file: str) -> int:
    result = publish_factor_catalog(
        create_postgres_engine(get_settings().database_url), Path(catalog_file)
    )
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    return 0


def _factor_bootstrap_engine(
    git_commit: str, dependency_lock_file: str, version_number: int
) -> int:
    settings = get_settings()
    engine = create_postgres_engine(settings.database_url)
    status = database_status(settings.database_url)
    if status.current_revision is None:
        raise ValueError("Database must be migrated before publishing a factor engine")
    spec = build_factor_engine_spec(
        git_commit,
        Path(dependency_lock_file),
        status.current_revision,
        version_number=version_number,
    )
    result = publish_factor_engine(engine, spec)
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    return 0


def _factor_publish(
    factor_catalog_artifact_id: str,
    bundle_artifact_id: str,
    eligibility_artifact_id: str,
    engine_artifact_id: str,
) -> int:
    results = FactorDatasetPublicationService(
        create_postgres_engine(get_settings().database_url)
    ).publish(
        uuid.UUID(factor_catalog_artifact_id),
        uuid.UUID(bundle_artifact_id),
        uuid.UUID(eligibility_artifact_id),
        uuid.UUID(engine_artifact_id),
    )
    print(json.dumps([item.to_dict() for item in results], indent=2, ensure_ascii=False))
    return 0


def _factor_bootstrap_diagnostic_engine(
    git_commit: str, dependency_lock_file: str, version_number: int
) -> int:
    settings = get_settings()
    engine = create_postgres_engine(settings.database_url)
    status = database_status(settings.database_url)
    if status.current_revision is None:
        raise ValueError("Database must be migrated before publishing a diagnostic engine")
    spec = build_factor_diagnostic_engine_spec(
        git_commit,
        Path(dependency_lock_file),
        status.current_revision,
        version_number=version_number,
    )
    result = publish_factor_diagnostic_engine(engine, spec)
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    return 0


def _factor_diagnose(
    factor_catalog_artifact_id: str,
    bundle_artifact_id: str,
    eligibility_artifact_id: str,
    factor_engine_artifact_id: str,
    diagnostic_engine_artifact_id: str,
) -> int:
    result = FactorDiagnosticPublicationService(
        create_postgres_engine(get_settings().database_url)
    ).publish(
        uuid.UUID(factor_catalog_artifact_id),
        uuid.UUID(bundle_artifact_id),
        uuid.UUID(eligibility_artifact_id),
        uuid.UUID(factor_engine_artifact_id),
        uuid.UUID(diagnostic_engine_artifact_id),
    )
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    return 0


def _signal_bootstrap(catalog_file: str) -> int:
    result = publish_signal_catalog(
        create_postgres_engine(get_settings().database_url), Path(catalog_file)
    )
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    return 0


def _model_bootstrap(catalog_file: str) -> int:
    result = publish_model_catalog(
        create_postgres_engine(get_settings().database_url), Path(catalog_file)
    )
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    return 0


def _strategy_bootstrap(catalog_file: str) -> int:
    result = publish_strategy_catalog(
        create_postgres_engine(get_settings().database_url), Path(catalog_file)
    )
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    return 0


def _strategy_publish_product(
    strategy_catalog_artifact_id: str,
    model_catalog_artifact_id: str,
    universe_artifact_id: str,
    model_specification_key: str,
    strategy_variant_key: str,
    schedule_key: str,
) -> int:
    result = publish_strategy_product(
        create_postgres_engine(get_settings().database_url),
        uuid.UUID(strategy_catalog_artifact_id),
        uuid.UUID(model_catalog_artifact_id),
        uuid.UUID(universe_artifact_id),
        model_specification_key,
        strategy_variant_key,
        schedule_key,
    )
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    return 0


def _strategy_bootstrap_target_engine(
    git_commit: str, dependency_lock_file: str, version_number: int
) -> int:
    settings = get_settings()
    status = database_status(settings.database_url)
    if status.current_revision is None:
        raise ValueError("Database must be migrated before publishing a Strategy Target engine")
    spec = build_strategy_target_engine_spec(
        git_commit,
        Path(dependency_lock_file),
        status.current_revision,
        version_number=version_number,
    )
    result = publish_strategy_target_engine(create_postgres_engine(settings.database_url), spec)
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    return 0


def _strategy_publish_target(
    product_artifact_id: str,
    model_dataset_artifact_id: str,
    target_engine_artifact_id: str,
    auxiliary_signal_dataset_artifact_id: str | None,
) -> int:
    result = StrategyTargetPublicationService(
        create_postgres_engine(get_settings().database_url)
    ).publish(
        uuid.UUID(product_artifact_id),
        uuid.UUID(model_dataset_artifact_id),
        uuid.UUID(target_engine_artifact_id),
        (
            uuid.UUID(auxiliary_signal_dataset_artifact_id)
            if auxiliary_signal_dataset_artifact_id is not None
            else None
        ),
    )
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    return 0


def _model_bootstrap_engine(git_commit: str, dependency_lock_file: str, version_number: int) -> int:
    settings = get_settings()
    status = database_status(settings.database_url)
    if status.current_revision is None:
        raise ValueError("Database must be migrated before publishing a Model engine")
    spec = build_model_engine_spec(
        git_commit,
        Path(dependency_lock_file),
        status.current_revision,
        version_number=version_number,
    )
    result = publish_model_engine(create_postgres_engine(settings.database_url), spec)
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    return 0


def _model_publish(
    model_catalog_artifact_id: str,
    signal_catalog_artifact_id: str,
    bundle_artifact_id: str,
    eligibility_artifact_id: str,
    signal_engine_artifact_id: str,
    model_engine_artifact_id: str,
) -> int:
    results = ModelDatasetPublicationService(
        create_postgres_engine(get_settings().database_url)
    ).publish(
        uuid.UUID(model_catalog_artifact_id),
        uuid.UUID(signal_catalog_artifact_id),
        uuid.UUID(bundle_artifact_id),
        uuid.UUID(eligibility_artifact_id),
        uuid.UUID(signal_engine_artifact_id),
        uuid.UUID(model_engine_artifact_id),
    )
    print(json.dumps([item.to_dict() for item in results], indent=2, ensure_ascii=False))
    return 0


def _model_bootstrap_evaluation_engine(
    git_commit: str, dependency_lock_file: str, version_number: int
) -> int:
    settings = get_settings()
    status = database_status(settings.database_url)
    if status.current_revision is None:
        raise ValueError("Database must be migrated before publishing a Model evaluation engine")
    spec = build_model_evaluation_engine_spec(
        git_commit,
        Path(dependency_lock_file),
        status.current_revision,
        version_number=version_number,
    )
    result = publish_model_evaluation_engine(create_postgres_engine(settings.database_url), spec)
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    return 0


def _model_evaluate(
    model_catalog_artifact_id: str,
    forward_return_artifact_id: str,
    model_engine_artifact_id: str,
    evaluation_engine_artifact_id: str,
) -> int:
    result = ModelDiagnosticPublicationService(
        create_postgres_engine(get_settings().database_url)
    ).publish(
        uuid.UUID(model_catalog_artifact_id),
        uuid.UUID(forward_return_artifact_id),
        uuid.UUID(model_engine_artifact_id),
        uuid.UUID(evaluation_engine_artifact_id),
    )
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    return 0


def _signal_bootstrap_engine(
    git_commit: str, dependency_lock_file: str, version_number: int
) -> int:
    settings = get_settings()
    status = database_status(settings.database_url)
    if status.current_revision is None:
        raise ValueError("Database must be migrated before publishing a signal engine")
    spec = build_signal_engine_spec(
        git_commit,
        Path(dependency_lock_file),
        status.current_revision,
        version_number=version_number,
    )
    result = publish_signal_engine(create_postgres_engine(settings.database_url), spec)
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    return 0


def _signal_publish(
    signal_catalog_artifact_id: str,
    factor_catalog_artifact_id: str,
    bundle_artifact_id: str,
    eligibility_artifact_id: str,
    factor_engine_artifact_id: str,
    signal_engine_artifact_id: str,
) -> int:
    results = SignalDatasetPublicationService(
        create_postgres_engine(get_settings().database_url)
    ).publish(
        uuid.UUID(signal_catalog_artifact_id),
        uuid.UUID(factor_catalog_artifact_id),
        uuid.UUID(bundle_artifact_id),
        uuid.UUID(eligibility_artifact_id),
        uuid.UUID(factor_engine_artifact_id),
        uuid.UUID(signal_engine_artifact_id),
    )
    print(json.dumps([item.to_dict() for item in results], indent=2, ensure_ascii=False))
    return 0


def _signal_bootstrap_evaluation_engine(
    git_commit: str, dependency_lock_file: str, version_number: int
) -> int:
    settings = get_settings()
    status = database_status(settings.database_url)
    if status.current_revision is None:
        raise ValueError("Database must be migrated before publishing an evaluation engine")
    spec = build_signal_evaluation_engine_spec(
        git_commit,
        Path(dependency_lock_file),
        status.current_revision,
        version_number=version_number,
    )
    result = publish_signal_evaluation_engine(create_postgres_engine(settings.database_url), spec)
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    return 0


def _signal_evaluate(
    signal_catalog_artifact_id: str,
    forward_return_artifact_id: str,
    signal_engine_artifact_id: str,
    evaluation_engine_artifact_id: str,
) -> int:
    result = SignalDiagnosticPublicationService(
        create_postgres_engine(get_settings().database_url)
    ).publish(
        uuid.UUID(signal_catalog_artifact_id),
        uuid.UUID(forward_return_artifact_id),
        uuid.UUID(signal_engine_artifact_id),
        uuid.UUID(evaluation_engine_artifact_id),
    )
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    return 0


def _forward_return_bootstrap(catalog_file: str) -> int:
    result = publish_forward_return_catalog(
        create_postgres_engine(get_settings().database_url), Path(catalog_file)
    )
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    return 0


def _forward_return_bootstrap_engine(
    git_commit: str, dependency_lock_file: str, version_number: int
) -> int:
    settings = get_settings()
    status = database_status(settings.database_url)
    if status.current_revision is None:
        raise ValueError("Database must be migrated before publishing a forward-return engine")
    spec = build_forward_return_engine_spec(
        git_commit,
        Path(dependency_lock_file),
        status.current_revision,
        version_number=version_number,
    )
    result = publish_forward_return_engine(create_postgres_engine(settings.database_url), spec)
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    return 0


def _forward_return_publish(
    catalog_artifact_id: str,
    universe_artifact_id: str,
    bundle_artifact_id: str,
    engine_artifact_id: str,
    start: date,
    end: date,
) -> int:
    results = ForwardReturnDatasetPublicationService(
        create_postgres_engine(get_settings().database_url)
    ).publish(
        uuid.UUID(catalog_artifact_id),
        uuid.UUID(universe_artifact_id),
        uuid.UUID(bundle_artifact_id),
        uuid.UUID(engine_artifact_id),
        requested_start=start,
        requested_end=end,
    )
    print(json.dumps([item.to_dict() for item in results], indent=2, ensure_ascii=False))
    return 0


def _publish_reserve(
    rate_dataset_artifact_id: str,
    calendar_artifact_id: str,
    model_artifact_id: str,
    version_number: int,
) -> int:
    result = ReservePublicationService(create_postgres_engine(get_settings().database_url)).publish(
        uuid.UUID(rate_dataset_artifact_id),
        uuid.UUID(calendar_artifact_id),
        uuid.UUID(model_artifact_id),
        version_number=version_number,
    )
    payload = asdict(result)
    payload["artifact_id"] = str(result.artifact_id)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def _publish_bundle(
    market_artifact_id: str,
    rate_artifact_id: str,
    reserve_artifact_id: str,
    calendar_artifact_id: str,
    version_number: int,
) -> int:
    results = publish_data_bundle(
        create_postgres_engine(get_settings().database_url),
        uuid.UUID(market_artifact_id),
        uuid.UUID(rate_artifact_id),
        uuid.UUID(reserve_artifact_id),
        uuid.UUID(calendar_artifact_id),
        version_number=version_number,
    )
    payload = []
    for result in results:
        item = asdict(result)
        item["artifact_id"] = str(result.artifact_id)
        payload.append(item)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def _publish_eligibility(
    universe_artifact_id: str,
    requirement_artifact_id: str,
    bundle_artifact_id: str,
    start: date,
    end_inclusive: date,
    warmup_observations: int,
    version_number: int,
) -> int:
    result = EligibilityPublicationService(
        create_postgres_engine(get_settings().database_url)
    ).publish(
        uuid.UUID(universe_artifact_id),
        uuid.UUID(requirement_artifact_id),
        uuid.UUID(bundle_artifact_id),
        requested_start=start,
        requested_end=end_inclusive,
        warmup_observations=warmup_observations,
        version_number=version_number,
    )
    payload = asdict(result)
    payload["artifact_id"] = str(result.artifact_id)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def _artifact_list() -> int:
    print(json.dumps(_artifact_service().list_artifacts(), indent=2, ensure_ascii=False))
    return 0


def _artifact_show(artifact_id: str) -> int:
    payload = _artifact_service().describe(uuid.UUID(artifact_id))
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def _artifact_invalidate(artifact_id: str, reason: str, replacement_artifact_id: str | None) -> int:
    tainted = _artifact_service().invalidate(
        uuid.UUID(artifact_id),
        reason,
        uuid.UUID(replacement_artifact_id) if replacement_artifact_id else None,
    )
    print(
        json.dumps(
            {"invalidated_artifact_id": artifact_id, "tainted_dependents": list(map(str, tainted))},
            indent=2,
        )
    )
    return 0


def _run_api(host: str, port: int) -> int:
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("The unauthenticated v0.2 API may only bind to a loopback address")
    import uvicorn

    uvicorn.run("style_rotation.api.app:app", host=host, port=port, reload=False)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="style-rotation",
        description="Versioned US style rotation research platform",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    modules_parser = subparsers.add_parser("modules", help="Show v0.2 domain boundaries")
    modules_parser.add_argument("--json", action="store_true", dest="as_json")
    modules_parser.set_defaults(handler=lambda args: _show_modules(args.as_json))

    db_parser = subparsers.add_parser("db", help="Database migration, reset, and status")
    db_subparsers = db_parser.add_subparsers(dest="db_command", required=True)
    db_status_parser = db_subparsers.add_parser("status", help="Show migration and schema status")
    db_status_parser.add_argument("--json", action="store_true", dest="as_json")
    db_status_parser.set_defaults(handler=lambda args: _db_status(args.as_json))
    db_upgrade_parser = db_subparsers.add_parser("upgrade", help="Upgrade to the v0.2 head")
    db_upgrade_parser.set_defaults(handler=lambda _args: _db_upgrade())
    db_reset_parser = db_subparsers.add_parser("reset", help="Destructively rebuild a local DB")
    db_reset_parser.add_argument("--confirm-database", required=True)
    db_reset_parser.set_defaults(handler=lambda args: _db_reset(args.confirm_database))

    bootstrap_parser = subparsers.add_parser("bootstrap", help="Publish research catalogs")
    bootstrap_subparsers = bootstrap_parser.add_subparsers(dest="bootstrap_command", required=True)
    catalogs_parser = bootstrap_subparsers.add_parser(
        "catalogs", help="Publish the M0 machine-readable catalogs"
    )
    catalogs_parser.add_argument("--catalog-dir", default="v0.2/catalogs")
    catalogs_parser.set_defaults(handler=lambda args: _bootstrap_catalogs(args.catalog_dir))
    scope_parser = bootstrap_subparsers.add_parser(
        "scope", help="Publish the M2A asset, universe, and data-requirement scope"
    )
    scope_parser.add_argument("--catalog-file", default="v0.2/catalogs/research_scope.v0.2.0.json")
    scope_parser.set_defaults(handler=lambda args: _bootstrap_scope(args.catalog_file))
    data_contracts_parser = bootstrap_subparsers.add_parser(
        "data-contracts", help="Publish M2B source, series, and cleaning contracts"
    )
    data_contracts_parser.add_argument(
        "--catalog-file", default="v0.2/catalogs/data_contracts.v0.2.0.json"
    )
    data_contracts_parser.set_defaults(
        handler=lambda args: _bootstrap_data_contracts(args.catalog_file)
    )
    reserve_model_parser = bootstrap_subparsers.add_parser(
        "reserve-model", help="Publish the versioned DGS3MO cash-accrual model"
    )
    reserve_model_parser.set_defaults(handler=lambda _args: _bootstrap_reserve_model())

    data_parser = subparsers.add_parser("data", help="Acquire immutable source evidence")
    data_subparsers = data_parser.add_subparsers(dest="data_command", required=True)
    fetch_parser = data_subparsers.add_parser(
        "fetch", help="Fetch and publish raw market/rate source snapshots"
    )
    fetch_parser.add_argument("--start", required=True, type=_parse_date)
    fetch_parser.add_argument("--end", required=True, type=_parse_date, dest="end_inclusive")
    fetch_parser.add_argument("--symbols", nargs="+", default=["IWF", "IWD", "IWO", "IWN", "SPY"])
    fetch_parser.add_argument("--skip-market", action="store_true")
    fetch_parser.add_argument("--skip-rate", action="store_true")
    fetch_parser.set_defaults(
        handler=lambda args: _data_fetch(
            args.start,
            args.end_inclusive,
            tuple(args.symbols),
            args.skip_market,
            args.skip_rate,
        )
    )
    calendar_parser = data_subparsers.add_parser(
        "calendar", help="Generate and publish a frozen XNYS calendar"
    )
    calendar_parser.add_argument("--start", required=True, type=_parse_date)
    calendar_parser.add_argument("--end", required=True, type=_parse_date, dest="end_inclusive")
    calendar_parser.add_argument("--version", type=int, default=1, dest="version_number")
    calendar_parser.set_defaults(
        handler=lambda args: _data_calendar(args.start, args.end_inclusive, args.version_number)
    )
    market_parser = data_subparsers.add_parser(
        "publish-market", help="Validate and publish a canonical daily-market dataset"
    )
    market_parser.add_argument("--snapshot-artifact-id", action="append", required=True)
    market_parser.add_argument("--calendar-artifact-id", required=True)
    market_parser.add_argument("--version", type=int, required=True, dest="version_number")
    market_parser.set_defaults(
        handler=lambda args: _canonical_market(
            tuple(args.snapshot_artifact_id),
            args.calendar_artifact_id,
            args.version_number,
        )
    )
    rate_parser = data_subparsers.add_parser(
        "publish-rate", help="Validate and publish a canonical DGS3MO dataset"
    )
    rate_parser.add_argument("--snapshot-artifact-id", required=True)
    rate_parser.add_argument("--version", type=int, required=True, dest="version_number")
    rate_parser.set_defaults(
        handler=lambda args: _canonical_rate(args.snapshot_artifact_id, args.version_number)
    )
    reserve_parser = data_subparsers.add_parser(
        "publish-reserve", help="Publish derived ACT/365 reserve-return intervals"
    )
    reserve_parser.add_argument("--rate-dataset-artifact-id", required=True)
    reserve_parser.add_argument("--calendar-artifact-id", required=True)
    reserve_parser.add_argument("--model-artifact-id", required=True)
    reserve_parser.add_argument("--version", type=int, required=True, dest="version_number")
    reserve_parser.set_defaults(
        handler=lambda args: _publish_reserve(
            args.rate_dataset_artifact_id,
            args.calendar_artifact_id,
            args.model_artifact_id,
            args.version_number,
        )
    )
    bundle_parser = data_subparsers.add_parser(
        "publish-bundle", help="Publish a fixed research data bundle"
    )
    bundle_parser.add_argument("--market-artifact-id", required=True)
    bundle_parser.add_argument("--rate-artifact-id", required=True)
    bundle_parser.add_argument("--reserve-artifact-id", required=True)
    bundle_parser.add_argument("--calendar-artifact-id", required=True)
    bundle_parser.add_argument("--version", type=int, required=True, dest="version_number")
    bundle_parser.set_defaults(
        handler=lambda args: _publish_bundle(
            args.market_artifact_id,
            args.rate_artifact_id,
            args.reserve_artifact_id,
            args.calendar_artifact_id,
            args.version_number,
        )
    )
    eligibility_parser = data_subparsers.add_parser(
        "publish-eligibility", help="Publish universe eligibility for one requested range"
    )
    eligibility_parser.add_argument("--universe-artifact-id", required=True)
    eligibility_parser.add_argument("--requirement-artifact-id", required=True)
    eligibility_parser.add_argument("--bundle-artifact-id", required=True)
    eligibility_parser.add_argument("--start", required=True, type=_parse_date)
    eligibility_parser.add_argument("--end", required=True, type=_parse_date, dest="end_inclusive")
    eligibility_parser.add_argument("--warmup-observations", type=int, default=253)
    eligibility_parser.add_argument("--version", type=int, required=True, dest="version_number")
    eligibility_parser.set_defaults(
        handler=lambda args: _publish_eligibility(
            args.universe_artifact_id,
            args.requirement_artifact_id,
            args.bundle_artifact_id,
            args.start,
            args.end_inclusive,
            args.warmup_observations,
            args.version_number,
        )
    )
    target_parser = data_subparsers.add_parser(
        "bootstrap-forward-returns", help="Materialize versioned forward-return targets"
    )
    target_parser.add_argument(
        "--catalog-file", default="v0.2/catalogs/forward_returns.v0.2.0.json"
    )
    target_parser.set_defaults(handler=lambda args: _forward_return_bootstrap(args.catalog_file))
    target_engine_parser = data_subparsers.add_parser(
        "bootstrap-forward-return-engine",
        help="Publish the deterministic forward-return engine version",
    )
    target_engine_parser.add_argument("--git-commit", required=True)
    target_engine_parser.add_argument("--dependency-lock-file", default="requirements.lock")
    target_engine_parser.add_argument("--version", type=int, default=1)
    target_engine_parser.set_defaults(
        handler=lambda args: _forward_return_bootstrap_engine(
            args.git_commit, args.dependency_lock_file, args.version
        )
    )
    target_publish_parser = data_subparsers.add_parser(
        "publish-forward-returns", help="Calculate immutable forward-return datasets"
    )
    target_publish_parser.add_argument("--catalog-artifact-id", required=True)
    target_publish_parser.add_argument("--universe-artifact-id", required=True)
    target_publish_parser.add_argument("--bundle-artifact-id", required=True)
    target_publish_parser.add_argument("--engine-artifact-id", required=True)
    target_publish_parser.add_argument("--start", required=True, type=_parse_date)
    target_publish_parser.add_argument("--end", required=True, type=_parse_date)
    target_publish_parser.set_defaults(
        handler=lambda args: _forward_return_publish(
            args.catalog_artifact_id,
            args.universe_artifact_id,
            args.bundle_artifact_id,
            args.engine_artifact_id,
            args.start,
            args.end,
        )
    )

    artifact_parser = subparsers.add_parser("artifact", help="Inspect artifact identity/status")
    artifact_subparsers = artifact_parser.add_subparsers(dest="artifact_command", required=True)
    artifact_list_parser = artifact_subparsers.add_parser("list", help="List artifacts")
    artifact_list_parser.set_defaults(handler=lambda _args: _artifact_list())
    artifact_show_parser = artifact_subparsers.add_parser("show", help="Show one artifact")
    artifact_show_parser.add_argument("artifact_id")
    artifact_show_parser.set_defaults(handler=lambda args: _artifact_show(args.artifact_id))
    invalidate_parser = artifact_subparsers.add_parser(
        "invalidate", help="Invalidate one artifact and taint downstream dependents"
    )
    invalidate_parser.add_argument("artifact_id")
    invalidate_parser.add_argument("--reason", required=True)
    invalidate_parser.add_argument("--replacement-artifact-id")
    invalidate_parser.set_defaults(
        handler=lambda args: _artifact_invalidate(
            args.artifact_id, args.reason, args.replacement_artifact_id
        )
    )

    lineage_parser = subparsers.add_parser("lineage", help="Inspect immutable lineage manifests")
    lineage_subparsers = lineage_parser.add_subparsers(dest="lineage_command", required=True)
    lineage_show_parser = lineage_subparsers.add_parser("show", help="Show expanded lineage")
    lineage_show_parser.add_argument("artifact_id")
    lineage_show_parser.set_defaults(handler=lambda args: _artifact_show(args.artifact_id))

    api_parser = subparsers.add_parser("api", help="Run the local read-only API and application")
    api_parser.add_argument("--host", default=get_settings().api_host)
    api_parser.add_argument("--port", type=int, default=get_settings().api_port)
    api_parser.set_defaults(handler=lambda args: _run_api(args.host, args.port))

    factor_parser = subparsers.add_parser(
        "factor", help="Materialize factor definitions and publish factor datasets"
    )
    factor_subparsers = factor_parser.add_subparsers(dest="factor_command", required=True)
    factor_bootstrap_parser = factor_subparsers.add_parser(
        "bootstrap", help="Materialize the published M0 factor catalog"
    )
    factor_bootstrap_parser.add_argument(
        "--catalog-file", default="v0.2/catalogs/factors.v0.2.0.json"
    )
    factor_bootstrap_parser.set_defaults(handler=lambda args: _factor_bootstrap(args.catalog_file))
    factor_engine_parser = factor_subparsers.add_parser(
        "bootstrap-engine", help="Publish the deterministic factor engine version"
    )
    factor_engine_parser.add_argument("--git-commit", required=True)
    factor_engine_parser.add_argument("--dependency-lock-file", default="requirements.lock")
    factor_engine_parser.add_argument("--version", type=int, default=1)
    factor_engine_parser.set_defaults(
        handler=lambda args: _factor_bootstrap_engine(
            args.git_commit, args.dependency_lock_file, args.version
        )
    )
    factor_publish_parser = factor_subparsers.add_parser(
        "publish", help="Calculate and publish all variants in a factor catalog"
    )
    factor_publish_parser.add_argument("--factor-catalog-artifact-id", required=True)
    factor_publish_parser.add_argument("--bundle-artifact-id", required=True)
    factor_publish_parser.add_argument("--eligibility-artifact-id", required=True)
    factor_publish_parser.add_argument("--engine-artifact-id", required=True)
    factor_publish_parser.set_defaults(
        handler=lambda args: _factor_publish(
            args.factor_catalog_artifact_id,
            args.bundle_artifact_id,
            args.eligibility_artifact_id,
            args.engine_artifact_id,
        )
    )
    factor_diagnostic_engine_parser = factor_subparsers.add_parser(
        "bootstrap-diagnostic-engine", help="Publish the factor diagnostic engine version"
    )
    factor_diagnostic_engine_parser.add_argument("--git-commit", required=True)
    factor_diagnostic_engine_parser.add_argument(
        "--dependency-lock-file", default="requirements.lock"
    )
    factor_diagnostic_engine_parser.add_argument("--version", type=int, default=1)
    factor_diagnostic_engine_parser.set_defaults(
        handler=lambda args: _factor_bootstrap_diagnostic_engine(
            args.git_commit, args.dependency_lock_file, args.version
        )
    )
    factor_diagnose_parser = factor_subparsers.add_parser(
        "diagnose", help="Publish factor-layer distribution and correlation diagnostics"
    )
    factor_diagnose_parser.add_argument("--factor-catalog-artifact-id", required=True)
    factor_diagnose_parser.add_argument("--bundle-artifact-id", required=True)
    factor_diagnose_parser.add_argument("--eligibility-artifact-id", required=True)
    factor_diagnose_parser.add_argument("--factor-engine-artifact-id", required=True)
    factor_diagnose_parser.add_argument("--diagnostic-engine-artifact-id", required=True)
    factor_diagnose_parser.set_defaults(
        handler=lambda args: _factor_diagnose(
            args.factor_catalog_artifact_id,
            args.bundle_artifact_id,
            args.eligibility_artifact_id,
            args.factor_engine_artifact_id,
            args.diagnostic_engine_artifact_id,
        )
    )

    signal_parser = subparsers.add_parser(
        "signal", help="Materialize versioned signal definitions and transformations"
    )
    signal_subparsers = signal_parser.add_subparsers(dest="signal_command", required=True)
    signal_bootstrap_parser = signal_subparsers.add_parser(
        "bootstrap", help="Materialize the published M0 signal catalog"
    )
    signal_bootstrap_parser.add_argument(
        "--catalog-file", default="v0.2/catalogs/signals.v0.2.0.json"
    )
    signal_bootstrap_parser.set_defaults(handler=lambda args: _signal_bootstrap(args.catalog_file))
    signal_engine_parser = signal_subparsers.add_parser(
        "bootstrap-engine", help="Publish the deterministic signal engine version"
    )
    signal_engine_parser.add_argument("--git-commit", required=True)
    signal_engine_parser.add_argument("--dependency-lock-file", default="requirements.lock")
    signal_engine_parser.add_argument("--version", type=int, default=1)
    signal_engine_parser.set_defaults(
        handler=lambda args: _signal_bootstrap_engine(
            args.git_commit, args.dependency_lock_file, args.version
        )
    )
    signal_publish_parser = signal_subparsers.add_parser(
        "publish", help="Calculate and publish all versions in a signal catalog"
    )
    signal_publish_parser.add_argument("--signal-catalog-artifact-id", required=True)
    signal_publish_parser.add_argument("--factor-catalog-artifact-id", required=True)
    signal_publish_parser.add_argument("--bundle-artifact-id", required=True)
    signal_publish_parser.add_argument("--eligibility-artifact-id", required=True)
    signal_publish_parser.add_argument("--factor-engine-artifact-id", required=True)
    signal_publish_parser.add_argument("--signal-engine-artifact-id", required=True)
    signal_publish_parser.set_defaults(
        handler=lambda args: _signal_publish(
            args.signal_catalog_artifact_id,
            args.factor_catalog_artifact_id,
            args.bundle_artifact_id,
            args.eligibility_artifact_id,
            args.factor_engine_artifact_id,
            args.signal_engine_artifact_id,
        )
    )
    signal_evaluation_engine_parser = signal_subparsers.add_parser(
        "bootstrap-evaluation-engine",
        help="Publish the deterministic Signal evaluation engine version",
    )
    signal_evaluation_engine_parser.add_argument("--git-commit", required=True)
    signal_evaluation_engine_parser.add_argument(
        "--dependency-lock-file", default="requirements.lock"
    )
    signal_evaluation_engine_parser.add_argument("--version", type=int, default=1)
    signal_evaluation_engine_parser.set_defaults(
        handler=lambda args: _signal_bootstrap_evaluation_engine(
            args.git_commit, args.dependency_lock_file, args.version
        )
    )
    signal_evaluate_parser = signal_subparsers.add_parser(
        "evaluate", help="Publish Signal IC, spread, stability, event, and redundancy diagnostics"
    )
    signal_evaluate_parser.add_argument("--signal-catalog-artifact-id", required=True)
    signal_evaluate_parser.add_argument("--forward-return-artifact-id", required=True)
    signal_evaluate_parser.add_argument("--signal-engine-artifact-id", required=True)
    signal_evaluate_parser.add_argument("--evaluation-engine-artifact-id", required=True)
    signal_evaluate_parser.set_defaults(
        handler=lambda args: _signal_evaluate(
            args.signal_catalog_artifact_id,
            args.forward_return_artifact_id,
            args.signal_engine_artifact_id,
            args.evaluation_engine_artifact_id,
        )
    )

    model_parser = subparsers.add_parser(
        "model", help="Materialize immutable model methods and specifications"
    )
    model_subparsers = model_parser.add_subparsers(dest="model_command", required=True)
    model_bootstrap_parser = model_subparsers.add_parser(
        "bootstrap", help="Materialize the published M0 model catalog"
    )
    model_bootstrap_parser.add_argument(
        "--catalog-file", default="v0.2/catalogs/models.v0.2.0.json"
    )
    model_bootstrap_parser.set_defaults(handler=lambda args: _model_bootstrap(args.catalog_file))
    model_engine_parser = model_subparsers.add_parser(
        "bootstrap-engine", help="Publish the deterministic Model engine version"
    )
    model_engine_parser.add_argument("--git-commit", required=True)
    model_engine_parser.add_argument("--dependency-lock-file", default="requirements.lock")
    model_engine_parser.add_argument("--version", type=int, default=1)
    model_engine_parser.set_defaults(
        handler=lambda args: _model_bootstrap_engine(
            args.git_commit, args.dependency_lock_file, args.version
        )
    )
    model_publish_parser = model_subparsers.add_parser(
        "publish", help="Calculate and publish every specification in a Model catalog"
    )
    model_publish_parser.add_argument("--model-catalog-artifact-id", required=True)
    model_publish_parser.add_argument("--signal-catalog-artifact-id", required=True)
    model_publish_parser.add_argument("--bundle-artifact-id", required=True)
    model_publish_parser.add_argument("--eligibility-artifact-id", required=True)
    model_publish_parser.add_argument("--signal-engine-artifact-id", required=True)
    model_publish_parser.add_argument("--model-engine-artifact-id", required=True)
    model_publish_parser.set_defaults(
        handler=lambda args: _model_publish(
            args.model_catalog_artifact_id,
            args.signal_catalog_artifact_id,
            args.bundle_artifact_id,
            args.eligibility_artifact_id,
            args.signal_engine_artifact_id,
            args.model_engine_artifact_id,
        )
    )
    model_evaluation_engine_parser = model_subparsers.add_parser(
        "bootstrap-evaluation-engine",
        help="Publish the deterministic Model evaluation engine version",
    )
    model_evaluation_engine_parser.add_argument("--git-commit", required=True)
    model_evaluation_engine_parser.add_argument(
        "--dependency-lock-file", default="requirements.lock"
    )
    model_evaluation_engine_parser.add_argument("--version", type=int, default=1)
    model_evaluation_engine_parser.set_defaults(
        handler=lambda args: _model_bootstrap_evaluation_engine(
            args.git_commit, args.dependency_lock_file, args.version
        )
    )
    model_evaluate_parser = model_subparsers.add_parser(
        "evaluate",
        help="Publish Model IC, stability, redundancy, dispersion, and ablation diagnostics",
    )
    model_evaluate_parser.add_argument("--model-catalog-artifact-id", required=True)
    model_evaluate_parser.add_argument("--forward-return-artifact-id", required=True)
    model_evaluate_parser.add_argument("--model-engine-artifact-id", required=True)
    model_evaluate_parser.add_argument("--evaluation-engine-artifact-id", required=True)
    model_evaluate_parser.set_defaults(
        handler=lambda args: _model_evaluate(
            args.model_catalog_artifact_id,
            args.forward_return_artifact_id,
            args.model_engine_artifact_id,
            args.evaluation_engine_artifact_id,
        )
    )

    strategy_parser = subparsers.add_parser(
        "strategy", help="Materialize immutable strategy contracts and variants"
    )
    strategy_subparsers = strategy_parser.add_subparsers(dest="strategy_command", required=True)
    strategy_bootstrap_parser = strategy_subparsers.add_parser(
        "bootstrap", help="Materialize the published M0 strategy catalog"
    )
    strategy_bootstrap_parser.add_argument(
        "--catalog-file", default="v0.2/catalogs/strategies.v0.2.0.json"
    )
    strategy_bootstrap_parser.set_defaults(
        handler=lambda args: _strategy_bootstrap(args.catalog_file)
    )
    strategy_product_parser = strategy_subparsers.add_parser(
        "publish-product", help="Publish one complete immutable Strategy Product identity"
    )
    strategy_product_parser.add_argument("--strategy-catalog-artifact-id", required=True)
    strategy_product_parser.add_argument("--model-catalog-artifact-id", required=True)
    strategy_product_parser.add_argument("--universe-artifact-id", required=True)
    strategy_product_parser.add_argument("--model-specification-key", required=True)
    strategy_product_parser.add_argument("--strategy-variant-key", required=True)
    strategy_product_parser.add_argument("--schedule-key", required=True)
    strategy_product_parser.set_defaults(
        handler=lambda args: _strategy_publish_product(
            args.strategy_catalog_artifact_id,
            args.model_catalog_artifact_id,
            args.universe_artifact_id,
            args.model_specification_key,
            args.strategy_variant_key,
            args.schedule_key,
        )
    )
    strategy_engine_parser = strategy_subparsers.add_parser(
        "bootstrap-target-engine", help="Publish the deterministic Strategy Target engine"
    )
    strategy_engine_parser.add_argument("--git-commit", required=True)
    strategy_engine_parser.add_argument("--dependency-lock-file", default="requirements.lock")
    strategy_engine_parser.add_argument("--version", type=int, default=1)
    strategy_engine_parser.set_defaults(
        handler=lambda args: _strategy_bootstrap_target_engine(
            args.git_commit, args.dependency_lock_file, args.version
        )
    )
    strategy_target_parser = strategy_subparsers.add_parser(
        "publish-target", help="Calculate and publish one immutable Strategy Target Path"
    )
    strategy_target_parser.add_argument("--product-artifact-id", required=True)
    strategy_target_parser.add_argument("--model-dataset-artifact-id", required=True)
    strategy_target_parser.add_argument("--target-engine-artifact-id", required=True)
    strategy_target_parser.add_argument("--auxiliary-signal-dataset-artifact-id")
    strategy_target_parser.set_defaults(
        handler=lambda args: _strategy_publish_target(
            args.product_artifact_id,
            args.model_dataset_artifact_id,
            args.target_engine_artifact_id,
            args.auxiliary_signal_dataset_artifact_id,
        )
    )

    for command in PLANNED_COMMANDS:
        if command.key in {
            "bootstrap",
            "data",
            "factor",
            "signal",
            "model",
            "strategy",
            "artifact",
            "lineage",
            "api",
        }:
            continue
        command_parser = subparsers.add_parser(command.key, help=command.summary)
        command_parser.set_defaults(handler=lambda _args, item=command: _planned(item))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handler = args.handler
    result: int = handler(args)
    return result


def run() -> NoReturn:
    try:
        exit_code = main()
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        exit_code = 2
    raise SystemExit(exit_code)


if __name__ == "__main__":
    run()
