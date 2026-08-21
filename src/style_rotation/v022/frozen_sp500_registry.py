from __future__ import annotations

import json
import uuid
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import date
from typing import Any, cast

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection, RowMapping

from style_rotation.catalog.versioning import semantic_version_number
from style_rotation.lineage.service import ArtifactService, DependencyInput
from style_rotation.v022.asset_selection import (
    ExplicitAssetSelectionPublication,
    ExplicitAssetSelectionService,
)

FROZEN_SP500_REGISTRY_CATALOG_VERSION = "0.22.3"
_RELEASE_KEY = "v022_sp500_asset_registry"
_RISK_DATASET_KEY = "us_sp500_historical_daily_free_research_v1"
_RISK_DATASET_VERSION = 5
_DATASET_GATE_KEY = "sp500_free_research_v1"
_DATASET_GATE_VERSION = 4
_UNIVERSE_METHODOLOGY_KEY = "sp500_historical_membership_free_research"


@dataclass(frozen=True, slots=True)
class FrozenSp500RegistryPublication:
    asset_registry_release_id: uuid.UUID
    artifact_id: uuid.UUID
    profile_count: int
    selected_security_count: int
    selection: ExplicitAssetSelectionPublication
    reused: bool


class FrozenSp500RegistryPublicationService:
    """Publish the frozen S&P securities into the executable Asset Registry."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def publish(self, *, created_by: str) -> FrozenSp500RegistryPublication:
        if not created_by.strip():
            raise ValueError("Frozen S&P Asset Registry publisher is required")
        with self._engine.begin() as connection:
            inputs = self._load_inputs(connection)
            categories = self._load_categories(connection, inputs["base_release_id"])
            profiles = self._load_profiles(connection, inputs)
            sets = self._load_sets(connection, inputs["base_release_id"])
            document = {
                "contract_version": "v0.22.frozen_sp500_asset_registry.v1",
                "catalog_version": FROZEN_SP500_REGISTRY_CATALOG_VERSION,
                "as_of_date": inputs["risk_coverage_end"].isoformat(),
                "base_asset_registry_release_id": str(inputs["base_release_id"]),
                "risk_dataset_publication_id": str(inputs["risk_dataset_id"]),
                "dataset_gate_assessment_id": str(inputs["dataset_gate_assessment_id"]),
                "universe_history_id": str(inputs["universe_history_id"]),
                "categories": categories,
                "profiles": profiles,
                "asset_sets": sets,
            }
            release_id = uuid.uuid5(
                uuid.NAMESPACE_URL,
                "bird:v0.22:frozen-sp500-asset-registry:" + str(inputs["risk_dataset_id"]),
            )
            service = ArtifactService(cast(Engine, _BoundConnection(connection)))
            publication = service.publish(
                artifact_type="asset_registry_release",
                artifact_key=_RELEASE_KEY,
                version_number=semantic_version_number(FROZEN_SP500_REGISTRY_CATALOG_VERSION),
                semantic_payload=document,
                content_payload=document,
                dependencies=(
                    DependencyInput(inputs["base_artifact_id"], "base_asset_registry", 0),
                    DependencyInput(inputs["identity_artifact_id"], "security_identity", 1),
                    DependencyInput(inputs["universe_artifact_id"], "universe_history", 2),
                    DependencyInput(inputs["risk_artifact_id"], "canonical_market_dataset", 3),
                    DependencyInput(
                        inputs["dataset_gate_artifact_id"], "dataset_gate_assessment", 4
                    ),
                ),
                reason="publish executable v0.22 historical S&P Asset Registry",
                draft_writer=lambda bound, artifact_id: self._write_registry(
                    bound,
                    artifact_id,
                    release_id,
                    inputs["risk_coverage_end"],
                    categories,
                    profiles,
                    sets,
                ),
            )
            selected_ids = tuple(
                uuid.UUID(profile["security_id"])
                for profile in profiles
                if profile["risk_dataset_member"]
            )
            selection = ExplicitAssetSelectionService().publish(
                connection,
                asset_registry_release_id=release_id,
                security_ids=selected_ids,
                created_by=created_by,
            )
        return FrozenSp500RegistryPublication(
            release_id,
            publication.artifact_id,
            len(profiles),
            len(selected_ids),
            selection,
            publication.reused,
        )

    @staticmethod
    def _load_inputs(connection: Connection) -> RowMapping:
        row = (
            connection.execute(
                text(
                    """
                SELECT base.asset_registry_release_id AS base_release_id,
                       base.artifact_id AS base_artifact_id,
                       identity.artifact_id AS identity_artifact_id,
                       history.universe_history_id,history.artifact_id AS universe_artifact_id,
                       risk.dataset_publication_id AS risk_dataset_id,
                       risk.artifact_id AS risk_artifact_id,
                       risk.coverage_end AS risk_coverage_end,
                       gate.dataset_gate_assessment_id,
                       gate.artifact_id AS dataset_gate_artifact_id
                  FROM catalog.asset_registry_release base
                  JOIN lineage.artifact base_artifact
                    ON base_artifact.artifact_id=base.artifact_id
                   AND base_artifact.status='published'
                  JOIN catalog.master_data_release identity
                    ON identity.release_key='v022_sp500_frozen_asset_identity'
                  JOIN lineage.artifact identity_artifact
                    ON identity_artifact.artifact_id=identity.artifact_id
                   AND identity_artifact.status='published'
                  JOIN catalog.universe_methodology methodology
                    ON methodology.methodology_key=:methodology
                   AND methodology.version_number=2
                  JOIN catalog.universe_history history
                    ON history.universe_methodology_id=methodology.universe_methodology_id
                  JOIN catalog.v022_universe_history_ledger_binding history_binding
                    ON history_binding.universe_history_id=history.universe_history_id
                   AND history_binding.universe_history_artifact_id=history.artifact_id
                  JOIN lineage.artifact universe_artifact
                    ON universe_artifact.artifact_id=history.artifact_id
                   AND universe_artifact.status='published'
                  JOIN data.dataset_publication risk
                    ON risk.dataset_key=:risk_key AND risk.version_number=:risk_version
                  JOIN lineage.artifact risk_artifact
                    ON risk_artifact.artifact_id=risk.artifact_id
                   AND risk_artifact.status='published'
                  JOIN data.v022_dataset_gate_assessment gate
                    ON gate.dataset_publication_id=risk.dataset_publication_id
                   AND gate.gate_key=:gate_key
                   AND gate.version_number=:gate_version
                   AND gate.ranking_eligibility='rankable_research'
                  JOIN lineage.artifact gate_artifact
                    ON gate_artifact.artifact_id=gate.artifact_id
                   AND gate_artifact.status='published'
                 WHERE base.release_key<>:release_key
                 ORDER BY base.version_number DESC
                 LIMIT 1
                """
                ),
                {
                    "methodology": _UNIVERSE_METHODOLOGY_KEY,
                    "risk_key": _RISK_DATASET_KEY,
                    "risk_version": _RISK_DATASET_VERSION,
                    "gate_key": _DATASET_GATE_KEY,
                    "gate_version": _DATASET_GATE_VERSION,
                    "release_key": _RELEASE_KEY,
                },
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise LookupError("Frozen S&P Asset Registry prerequisites are incomplete")
        return row

    @staticmethod
    def _load_categories(
        connection: Connection, base_release_id: uuid.UUID
    ) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in connection.execute(
                text(
                    """
                    SELECT category_key,name,description,ordinal
                      FROM catalog.asset_category
                     WHERE asset_registry_release_id=:release
                     ORDER BY ordinal
                    """
                ),
                {"release": base_release_id},
            ).mappings()
        ]

    @staticmethod
    def _load_profiles(connection: Connection, inputs: RowMapping) -> list[dict[str, Any]]:
        rows = (
            connection.execute(
                text(
                    """
                WITH risk_members AS (
                  SELECT DISTINCT security.security_id
                    FROM data.daily_bar bar
                    JOIN catalog.security security ON security.legacy_asset_id=bar.asset_id
                   WHERE bar.dataset_publication_id=:risk_dataset
                ), base_profiles AS (
                  SELECT profile.security_id,profile.symbol,profile.aliases,
                         profile.asset_class,profile.instrument_type,profile.tradability,
                         profile.venue_mic,profile.calendar_key,profile.tags,
                         profile.maturity,profile.target_maturity,
                         profile.missing_requirements,profile.search_document,
                         category.category_key,false AS added
                    FROM catalog.security_profile profile
                    JOIN catalog.asset_category category
                      ON category.asset_category_id=profile.asset_category_id
                   WHERE profile.asset_registry_release_id=:base_release
                ), added_profiles AS (
                  SELECT security.security_id,identifier.identifier_value AS symbol,
                         jsonb_build_array(security.security_key) AS aliases,
                         'equity'::varchar AS asset_class,
                         'Common Stock'::varchar AS instrument_type,
                         'tradable'::varchar AS tradability,
                         NULL::varchar AS venue_mic,'XNYS'::varchar AS calendar_key,
                         '["sp500_historical","free_source","retrospective_price"]'::jsonb
                           AS tags,
                         'research_ready'::varchar AS maturity,
                         'product_eligible_input'::varchar AS target_maturity,
                         '["free_source_data_warning"]'::jsonb AS missing_requirements,
                         lower(concat_ws(' ',security.security_key,security.name,
                                               identifier.identifier_value)) AS search_document,
                         'stocks'::varchar AS category_key,true AS added
                    FROM risk_members risk
                    JOIN catalog.security security ON security.security_id=risk.security_id
                    JOIN catalog.security_identifier identifier
                      ON identifier.security_id=security.security_id
                     AND identifier.provider_scope='yahoo_yfinance'
                     AND identifier.identifier_type='provider_symbol'
                     AND identifier.valid_from IS NULL AND identifier.valid_to IS NULL
                   WHERE NOT EXISTS (
                     SELECT 1 FROM base_profiles base
                      WHERE base.security_id=security.security_id
                   )
                )
                SELECT profile.*,(risk.security_id IS NOT NULL) AS risk_dataset_member
                  FROM (
                    SELECT * FROM base_profiles
                    UNION ALL
                    SELECT * FROM added_profiles
                  ) profile
                  LEFT JOIN risk_members risk ON risk.security_id=profile.security_id
                 ORDER BY lower(profile.symbol),profile.security_id
                """
                ),
                {
                    "risk_dataset": inputs["risk_dataset_id"],
                    "base_release": inputs["base_release_id"],
                },
            )
            .mappings()
            .all()
        )
        profiles = [
            {
                **dict(row),
                "security_id": str(row["security_id"]),
                "aliases": list(row["aliases"]),
                "tags": list(row["tags"]),
                "missing_requirements": list(row["missing_requirements"]),
            }
            for row in rows
        ]
        FrozenSp500RegistryPublicationService._validate_profiles(profiles)
        return profiles

    @staticmethod
    def _validate_profiles(profiles: list[dict[str, Any]]) -> None:
        symbols = [str(item["symbol"]).casefold() for item in profiles]
        if len(symbols) != len(set(symbols)):
            raise ValueError("Frozen S&P Asset Registry contains duplicate symbols")
        if not profiles or not any(item["risk_dataset_member"] for item in profiles):
            raise ValueError("Frozen S&P Asset Registry has no executable risk members")

    @staticmethod
    def _load_sets(connection: Connection, base_release_id: uuid.UUID) -> list[dict[str, Any]]:
        rows = (
            connection.execute(
                text(
                    """
                SELECT definition.set_key,definition.name,definition.set_type,
                       definition.maturity,definition.formal_eligible,definition.notes,
                       COALESCE(jsonb_agg(member.security_id ORDER BY member.ordinal)
                         FILTER (WHERE member.security_id IS NOT NULL),'[]'::jsonb) AS members
                  FROM catalog.asset_set_definition definition
                  LEFT JOIN catalog.asset_set_member member
                    ON member.asset_set_definition_id=definition.asset_set_definition_id
                 WHERE definition.asset_registry_release_id=:release
                 GROUP BY definition.asset_set_definition_id
                 ORDER BY definition.set_key
                """
                ),
                {"release": base_release_id},
            )
            .mappings()
            .all()
        )
        return [{**dict(row), "members": [str(value) for value in row["members"]]} for row in rows]

    @staticmethod
    def _write_registry(
        connection: Connection,
        artifact_id: uuid.UUID,
        release_id: uuid.UUID,
        as_of_date: date,
        categories: list[dict[str, Any]],
        profiles: list[dict[str, Any]],
        sets: list[dict[str, Any]],
    ) -> None:
        connection.execute(
            text(
                """
                INSERT INTO catalog.asset_registry_release (
                  asset_registry_release_id,artifact_id,release_key,version_number,
                  catalog_version,as_of_date
                ) VALUES (:id,:artifact,:key,:version,:catalog_version,:as_of)
                """
            ),
            {
                "id": release_id,
                "artifact": artifact_id,
                "key": _RELEASE_KEY,
                "version": semantic_version_number(FROZEN_SP500_REGISTRY_CATALOG_VERSION),
                "catalog_version": FROZEN_SP500_REGISTRY_CATALOG_VERSION,
                "as_of": as_of_date,
            },
        )
        category_ids: dict[str, uuid.UUID] = {}
        for item in categories:
            category_id = uuid.uuid5(release_id, "asset-category:" + str(item["category_key"]))
            category_ids[str(item["category_key"])] = category_id
            connection.execute(
                text(
                    """
                    INSERT INTO catalog.asset_category (
                      asset_category_id,asset_registry_release_id,category_key,
                      name,description,ordinal
                    ) VALUES (:id,:release,:key,:name,:description,:ordinal)
                    """
                ),
                {
                    "id": category_id,
                    "release": release_id,
                    "key": item["category_key"],
                    "name": item["name"],
                    "description": item["description"],
                    "ordinal": item["ordinal"],
                },
            )
        for ordinal, item in enumerate(profiles):
            security_id = uuid.UUID(str(item["security_id"]))
            connection.execute(
                text(
                    """
                    INSERT INTO catalog.security_profile (
                      security_profile_id,asset_registry_release_id,security_id,
                      asset_category_id,symbol,aliases,asset_class,instrument_type,
                      tradability,venue_mic,calendar_key,tags,maturity,target_maturity,
                      missing_requirements,search_document,ordinal
                    ) VALUES (
                      :id,:release,:security,:category,:symbol,CAST(:aliases AS jsonb),
                      :asset_class,:instrument_type,:tradability,:venue,:calendar,
                      CAST(:tags AS jsonb),:maturity,:target_maturity,
                      CAST(:missing AS jsonb),:search_document,:ordinal
                    )
                    """
                ),
                {
                    "id": uuid.uuid5(release_id, "security-profile:" + str(security_id)),
                    "release": release_id,
                    "security": security_id,
                    "category": category_ids[str(item["category_key"])],
                    "symbol": item["symbol"],
                    "aliases": json.dumps(item["aliases"], sort_keys=True),
                    "asset_class": item["asset_class"],
                    "instrument_type": item["instrument_type"],
                    "tradability": item["tradability"],
                    "venue": item["venue_mic"],
                    "calendar": item["calendar_key"],
                    "tags": json.dumps(item["tags"], sort_keys=True),
                    "maturity": item["maturity"],
                    "target_maturity": item["target_maturity"],
                    "missing": json.dumps(item["missing_requirements"], sort_keys=True),
                    "search_document": item["search_document"],
                    "ordinal": ordinal,
                },
            )
        for item in sets:
            definition_id = uuid.uuid5(release_id, "asset-set:" + str(item["set_key"]))
            connection.execute(
                text(
                    """
                    INSERT INTO catalog.asset_set_definition (
                      asset_set_definition_id,asset_registry_release_id,set_key,name,
                      set_type,maturity,formal_eligible,notes
                    ) VALUES (:id,:release,:key,:name,:type,:maturity,:formal,:notes)
                    """
                ),
                {
                    "id": definition_id,
                    "release": release_id,
                    "key": item["set_key"],
                    "name": item["name"],
                    "type": item["set_type"],
                    "maturity": item["maturity"],
                    "formal": item["formal_eligible"],
                    "notes": item["notes"],
                },
            )
            for ordinal, member in enumerate(item["members"]):
                connection.execute(
                    text(
                        """
                        INSERT INTO catalog.asset_set_member (
                          asset_set_definition_id,security_id,ordinal
                        ) VALUES (:definition,:security,:ordinal)
                        """
                    ),
                    {
                        "definition": definition_id,
                        "security": uuid.UUID(str(member)),
                        "ordinal": ordinal,
                    },
                )


class _BoundConnection:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def begin(self) -> nullcontext[Connection]:
        return nullcontext(self._connection)
