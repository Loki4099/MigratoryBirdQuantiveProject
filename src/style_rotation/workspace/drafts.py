from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import Engine, text


class DraftRevisionConflict(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ResearchDraft:
    research_draft_id: uuid.UUID
    draft_key: str
    researcher_id: str
    name: str
    revision: int
    selection: dict[str, Any]
    last_compiled_artifact_id: uuid.UUID | None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["research_draft_id"] = str(self.research_draft_id)
        if self.last_compiled_artifact_id is not None:
            payload["last_compiled_artifact_id"] = str(self.last_compiled_artifact_id)
        return payload


class ResearchDraftService:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def get(self, *, researcher_id: str, draft_key: str) -> ResearchDraft | None:
        _validate_identity(researcher_id, draft_key)
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    text(
                        "SELECT * FROM workspace.research_draft "
                        "WHERE researcher_id = :researcher AND draft_key = :draft"
                    ),
                    {"researcher": researcher_id, "draft": draft_key},
                )
                .mappings()
                .one_or_none()
            )
        return _from_row(row) if row is not None else None

    def save(
        self,
        *,
        researcher_id: str,
        draft_key: str,
        name: str,
        selection: dict[str, Any],
        expected_revision: int | None,
    ) -> ResearchDraft:
        _validate_identity(researcher_id, draft_key)
        if not name.strip():
            raise ValueError("Draft name is required")
        _validate_selection(selection)
        with self._engine.begin() as connection:
            current = (
                connection.execute(
                    text(
                        "SELECT * FROM workspace.research_draft "
                        "WHERE researcher_id = :researcher AND draft_key = :draft FOR UPDATE"
                    ),
                    {"researcher": researcher_id, "draft": draft_key},
                )
                .mappings()
                .one_or_none()
            )
            if current is None:
                if expected_revision not in {None, 0}:
                    raise DraftRevisionConflict("Draft does not exist at expected revision")
                draft_id = uuid.uuid4()
                row = (
                    connection.execute(
                        text(
                            """
                        INSERT INTO workspace.research_draft (
                            research_draft_id, draft_key, researcher_id, name,
                            revision, selection
                        ) VALUES (
                            :id, :draft, :researcher, :name, 1,
                            CAST(:selection AS jsonb)
                        ) RETURNING *
                        """
                        ),
                        {
                            "id": draft_id,
                            "draft": draft_key,
                            "researcher": researcher_id,
                            "name": name.strip(),
                            "selection": _json(selection),
                        },
                    )
                    .mappings()
                    .one()
                )
                return _from_row(row)
            if expected_revision is None or current["revision"] != expected_revision:
                raise DraftRevisionConflict(
                    f"Draft revision conflict: current={current['revision']}"
                )
            row = (
                connection.execute(
                    text(
                        """
                    UPDATE workspace.research_draft
                    SET name = :name, selection = CAST(:selection AS jsonb),
                        revision = revision + 1, updated_at = now()
                    WHERE research_draft_id = :id RETURNING *
                    """
                    ),
                    {
                        "id": current["research_draft_id"],
                        "name": name.strip(),
                        "selection": _json(selection),
                    },
                )
                .mappings()
                .one()
            )
            return _from_row(row)

    def mark_compiled(
        self,
        *,
        researcher_id: str,
        draft_key: str,
        expected_revision: int,
        artifact_id: uuid.UUID,
    ) -> None:
        """Link the exact submitted Suite without changing the saved selection revision."""
        with self._engine.begin() as connection:
            updated = connection.execute(
                text("""
                    UPDATE workspace.research_draft
                    SET last_compiled_artifact_id = :artifact_id, updated_at = now()
                    WHERE researcher_id = :researcher AND draft_key = :draft
                      AND revision = :revision
                    RETURNING research_draft_id
                """),
                {
                    "artifact_id": artifact_id,
                    "researcher": researcher_id,
                    "draft": draft_key,
                    "revision": expected_revision,
                },
            ).scalar_one_or_none()
        if updated is None:
            raise DraftRevisionConflict("Draft changed while its Suite was being submitted")


def _validate_identity(researcher_id: str, draft_key: str) -> None:
    if not researcher_id.strip() or len(researcher_id) > 120:
        raise ValueError("Researcher id is required and must be at most 120 characters")
    if not draft_key.strip() or len(draft_key) > 180:
        raise ValueError("Draft key is required and must be at most 180 characters")


def _validate_selection(selection: dict[str, Any]) -> None:
    required = {
        "frequency",
        "asset_security_ids",
        "asset_data_inputs",
        "factor_variant_keys",
        "signal_version_keys",
        "model_preset_keys",
        "model_target_keys",
        "strategy_preset_keys",
    }
    legacy_without_target = required - {"model_target_keys"}
    legacy_without_inputs = required - {"asset_data_inputs"}
    legacy_without_both = required - {"model_target_keys", "asset_data_inputs"}
    if set(selection) == legacy_without_target or set(selection) == legacy_without_both:
        selection["model_target_keys"] = ["cross_sectional_relative_return__h5"]
    if set(selection) == legacy_without_inputs or set(selection) == legacy_without_both:
        selection["asset_data_inputs"] = {
            str(security_id): ["canonical_market_bars"]
            for security_id in selection["asset_security_ids"]
        }
    if set(selection) != required:
        raise ValueError("Draft selection has an unsupported shape")
    if selection["frequency"] not in {"weekly", "monthly"}:
        raise ValueError("Draft frequency must be weekly or monthly")
    for key in required - {"frequency", "asset_data_inputs"}:
        values = selection[key]
        if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
            raise ValueError(f"Draft {key} must be a list of strings")
        if len(values) != len(set(values)):
            raise ValueError(f"Draft {key} contains duplicates")
    input_mapping = selection["asset_data_inputs"]
    if not isinstance(input_mapping, dict):
        raise ValueError("Draft asset_data_inputs must be an object")
    if set(input_mapping) != set(selection["asset_security_ids"]):
        raise ValueError("Draft asset_data_inputs must exactly match selected assets")
    for security_id, input_keys in input_mapping.items():
        if not isinstance(security_id, str) or not isinstance(input_keys, list):
            raise ValueError("Draft asset_data_inputs must map security ids to lists")
        if any(not isinstance(item, str) or not item for item in input_keys):
            raise ValueError("Draft asset data-input keys must be non-empty strings")
        if len(input_keys) != len(set(input_keys)):
            raise ValueError(f"Draft asset {security_id} contains duplicate data inputs")


def _from_row(row: Any) -> ResearchDraft:
    selection = dict(row["selection"])
    selection.setdefault("model_target_keys", ["cross_sectional_relative_return__h5"])
    selection.setdefault(
        "asset_data_inputs",
        {
            str(security_id): ["canonical_market_bars"]
            for security_id in selection.get("asset_security_ids", [])
        },
    )
    return ResearchDraft(
        research_draft_id=row["research_draft_id"],
        draft_key=row["draft_key"],
        researcher_id=row["researcher_id"],
        name=row["name"],
        revision=row["revision"],
        selection=selection,
        last_compiled_artifact_id=row["last_compiled_artifact_id"],
    )


def _json(value: dict[str, Any]) -> str:
    import json

    return json.dumps(value, sort_keys=True, separators=(",", ":"))
