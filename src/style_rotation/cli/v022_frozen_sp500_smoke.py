from __future__ import annotations

import argparse
import json
import uuid
from typing import Any, Literal, cast

from sqlalchemy import text

from style_rotation.config.settings import get_settings
from style_rotation.persistence.session import create_postgres_engine
from style_rotation.v022.draft_service import GraphDraftService, GraphDraftSnapshot
from style_rotation.v022.green_baseline_registry import (
    GREEN_BASELINE_REGISTRY_CATALOG_VERSION,
)
from style_rotation.v022.suite_launch_batch import (
    SuiteLaunchBatchRequest,
    SuiteLaunchBatchService,
)
from style_rotation.v022.suite_runtime_commands import SuiteRuntimeCommandService
from style_rotation.v022.workspace_view import representative_workspace_service

_REGISTRY_RELEASE_KEY = "v022_sp500_asset_registry"
_SMOKE_FEATURE = "return_continuation__w120"
_SMOKE_AGGREGATION = "flat_equal_weight_mean"
_SMOKE_AGGREGATION_PRESET = "signal_equal_v1"
_STOCK_STRATEGY = "cross_section_rank_top_k_large_cap_multi_frequency"
_ETF_STRATEGY = "cross_section_rank_top_k_parity"
_SMOKE_DEFENSE = "none"
_SMOKE_VERSION = 16


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compile and submit the frozen weekly/monthly S&P v0.22 smoke experiments"
    )
    parser.add_argument("--actor", default="local")
    parser.add_argument("--frequency", choices=("weekly", "monthly", "both"), default="both")
    parser.add_argument("--feature", default=_SMOKE_FEATURE)
    parser.add_argument("--smoke-version", type=int, default=_SMOKE_VERSION)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.smoke_version < 1:
        raise ValueError("Smoke version must be positive")
    frequencies = ("weekly", "monthly") if args.frequency == "both" else (args.frequency,)
    engine = create_postgres_engine(get_settings().database_url)
    try:
        drafts = GraphDraftService(engine, representative_workspace_service())
        commands = SuiteRuntimeCommandService(engine)
        batches = SuiteLaunchBatchService(
            engine,
            graph_drafts=drafts,
            graph_suites=commands,
        )
        outputs: list[dict[str, Any]] = []
        for raw_frequency in frequencies:
            frequency = cast(Literal["weekly", "monthly"], raw_frequency)
            draft = drafts.create(
                researcher_key=args.actor,
                draft_key=(f"sp500_frozen_cohort_smoke_{frequency}_v{args.smoke_version}"),
                name=f"Frozen S&P 500 {frequency} cohort smoke",
                idempotency_key=_stable_id(f"create:v{args.smoke_version}:{frequency}"),
                frequency=frequency,
            )
            if draft.status == "draft":
                draft = _configure(
                    drafts,
                    engine,
                    draft,
                    actor=args.actor,
                    feature_key=args.feature,
                )
                compiled = drafts.current_compile(
                    draft.graph_draft_id, actor_key=args.actor
                ) or drafts.compile(
                    draft.graph_draft_id,
                    expected_revision=draft.revision,
                    actor_key=args.actor,
                    idempotency_key=_stable_id(
                        f"compile:v{args.smoke_version}:{frequency}:{args.feature}"
                    ),
                )
            else:
                existing_compile = drafts.current_compile(
                    draft.graph_draft_id, actor_key=args.actor
                )
                if existing_compile is None:
                    raise RuntimeError("Locked S&P smoke Draft has no current compile")
                compiled = existing_compile
            launched = batches.submit(
                SuiteLaunchBatchRequest(
                    actor_key=args.actor,
                    idempotency_key=_stable_id(
                        "launch:"
                        f"v{args.smoke_version}:{frequency}:{args.feature}:"
                        f"{compiled.compiled_research_graph_id}"
                    ),
                    source_graph_draft_id=draft.graph_draft_id,
                    source_graph_draft_revision=draft.revision,
                    source_compiled_research_graph_id=(
                        compiled.compiled_research_graph_id
                    ),
                    frequencies=(frequency,),
                    suite_mode="exploratory",
                )
            )
            child = launched["children"][0]
            outputs.append(
                {
                    "frequency": frequency,
                    "suite_launch_batch_id": str(launched["suite_launch_batch_id"]),
                    "graph_draft_id": str(draft.graph_draft_id),
                    "graph_draft_revision": draft.revision,
                    "compiled_research_graph_id": str(compiled.compiled_research_graph_id),
                    "research_suite_id": str(child["research_suite_id"]),
                    "status": child["status"],
                }
            )
    finally:
        engine.dispose()
    print(json.dumps({"submissions": outputs}, sort_keys=True, indent=2))
    return 0


def _configure(
    drafts: GraphDraftService,
    engine: Any,
    draft: GraphDraftSnapshot,
    *,
    actor: str,
    feature_key: str = _SMOKE_FEATURE,
) -> GraphDraftSnapshot:
    with engine.connect() as connection:
        security_ids = tuple(
            connection.scalars(
                text(
                    """
                    SELECT profile.security_id
                      FROM catalog.security_profile profile
                      JOIN catalog.asset_category category
                        ON category.asset_category_id=profile.asset_category_id
                      JOIN catalog.asset_registry_release release
                        ON release.asset_registry_release_id=
                           profile.asset_registry_release_id
                     WHERE release.release_key=:release_key
                       AND release.catalog_version=:catalog_version
                       AND category.category_key='stocks'
                       AND profile.tradability='tradable'
                       AND profile.target_maturity IN (
                         'research_ready','strategy_ready','product_eligible_input'
                       )
                     ORDER BY profile.ordinal
                    """
                ),
                {
                    "release_key": _REGISTRY_RELEASE_KEY,
                    "catalog_version": GREEN_BASELINE_REGISTRY_CATALOG_VERSION,
                },
            ).all()
        )
    if not security_ids:
        raise LookupError("Exact clean-green executable S&P selection not found")
    if draft.asset_context.get("selection_kind") != "explicit_security_selection":
        draft = _event(
            drafts,
            draft,
            actor,
            "set_asset_selection",
            {"security_ids": [str(value) for value in security_ids]},
        )
    selected = {
        (str(item["feature_key"]), int(item["stage_no"]))
        for item in draft.intent["explicit_features"]
    }
    if (feature_key, 3) not in selected:
        draft = _event(
            drafts,
            draft,
            actor,
            "select_feature_occurrence",
            {"feature_key": feature_key, "stage_no": 3},
        )
    if _SMOKE_AGGREGATION not in draft.intent["aggregation_family_keys"]:
        draft = _event(
            drafts,
            draft,
            actor,
            "select_aggregation_family",
            {"family_key": _SMOKE_AGGREGATION},
        )
    if draft.intent["aggregation_parameter_preset_keys"].get(
        _SMOKE_AGGREGATION
    ) != [_SMOKE_AGGREGATION_PRESET]:
        draft = _event(
            drafts,
            draft,
            actor,
            "set_aggregation_parameter_presets",
            {
                "family_key": _SMOKE_AGGREGATION,
                "preset_keys": [_SMOKE_AGGREGATION_PRESET],
            },
        )
    if draft.intent["strategy_parameter_preset_keys"].get(_ETF_STRATEGY):
        draft = _event(
            drafts,
            draft,
            actor,
            "set_strategy_parameter_presets",
            {"strategy_key": _ETF_STRATEGY, "preset_keys": []},
        )
    if draft.intent["strategy_parameter_preset_keys"].get(_STOCK_STRATEGY) != ["k10"]:
        draft = _event(
            drafts,
            draft,
            actor,
            "set_strategy_parameter_presets",
            {"strategy_key": _STOCK_STRATEGY, "preset_keys": ["k10"]},
        )
    if draft.intent["defense_keys"] != [_SMOKE_DEFENSE]:
        for defense_key in tuple(draft.intent["defense_keys"]):
            draft = _event(
                drafts,
                draft,
                actor,
                "deselect_defense",
                {"defense_key": defense_key},
            )
        draft = _event(
            drafts,
            draft,
            actor,
            "select_defense",
            {"defense_key": _SMOKE_DEFENSE},
        )
    return draft


def _event(
    drafts: GraphDraftService,
    draft: GraphDraftSnapshot,
    actor: str,
    event_type: str,
    event: dict[str, Any],
) -> GraphDraftSnapshot:
    return drafts.apply_event(
        draft.graph_draft_id,
        expected_revision=draft.revision,
        actor_key=actor,
        idempotency_key=uuid.uuid4(),
        event_type=event_type,
        event=event,
    ).snapshot


def _stable_id(value: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, "bird:v0.22:frozen-sp500-smoke:" + value)


if __name__ == "__main__":
    raise SystemExit(main())
