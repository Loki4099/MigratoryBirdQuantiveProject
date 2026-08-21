# ruff: noqa: E501
"""Add composed Defense Package and auxiliary execution-context identities.

Revision ID: 20260812_77_v022_defense_package
Revises: 20260812_76_v022_exec_context
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260812_77_v022_defense_package"
down_revision: str | None = "20260812_76_v022_exec_context"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_BASE_COMPONENT_KINDS = (
    "'payload_contract_family','payload_contract_version','payload_compatibility',"
    "'physical_encoding_version','feature_family','feature_variant','feature_version',"
    "'processing_node_definition','processing_node_variant','processing_node_version',"
    "'aggregation_family','aggregation_version',"
    "'aggregation_parameter_preset_definition',"
    "'aggregation_parameter_preset_version','aggregation_target_definition',"
    "'aggregation_target_version','aggregation_training_preset_definition',"
    "'aggregation_training_preset_version','strategy_family',"
    "'strategy_variant','strategy_version','defense_family','defense_variant',"
    "'defense_version','strategy_parameter_preset_definition',"
    "'strategy_parameter_preset_version'"
)
_DEFENSE_POLICY_COMPONENT_KINDS = (
    ",'defense_timing_family','defense_timing_variant','defense_timing_version'"
    ",'defense_allocation_family','defense_allocation_variant'"
    ",'defense_allocation_version'"
)
_COMPONENT_KIND_CONSTRAINT = (
    "ck_v022_catalog_release_component_ck_v022_component_kind"
)


def upgrade() -> None:
    _set_release_component_kinds(include_defense_policies=True)
    _create_policy_tables()
    _create_package_tables()
    _create_execution_context_tables()
    _create_policy_identity_guards()
    _create_policy_completeness_guards()
    _create_package_guards()
    _create_execution_context_guards()
    _create_append_only_guards()


def _set_release_component_kinds(*, include_defense_policies: bool) -> None:
    kinds = _BASE_COMPONENT_KINDS
    if include_defense_policies:
        kinds += _DEFENSE_POLICY_COMPONENT_KINDS
    op.execute(
        "ALTER TABLE workspace.v022_catalog_release_component "
        f"DROP CONSTRAINT {_COMPONENT_KIND_CONSTRAINT}"
    )
    op.execute(
        "ALTER TABLE workspace.v022_catalog_release_component "
        f"ADD CONSTRAINT {_COMPONENT_KIND_CONSTRAINT} "
        f"CHECK (component_kind IN ({kinds}))"
    )


def _create_policy_tables() -> None:
    op.execute(
        """
        CREATE TABLE defense.v022_timing_policy_family (
          timing_policy_family_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          family_key varchar(180) NOT NULL UNIQUE CHECK (btrim(family_key)<>''),
          name varchar(240) NOT NULL CHECK (btrim(name)<>''),
          formula_identity text NOT NULL CHECK (btrim(formula_identity)<>''),
          research_hypothesis text NOT NULL CHECK (btrim(research_hypothesis)<>''),
          created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE TABLE defense.v022_timing_policy_variant (
          timing_policy_variant_id uuid PRIMARY KEY,
          timing_policy_family_id uuid NOT NULL
            REFERENCES defense.v022_timing_policy_family,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          variant_key varchar(220) NOT NULL UNIQUE CHECK (btrim(variant_key)<>''),
          rule jsonb NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (timing_policy_variant_id,timing_policy_family_id),
          CHECK (jsonb_typeof(rule)='object' AND rule<>'{}'::jsonb),
          CHECK (rule->>'rule_type' IN (
            'fixed_budget','moving_average_tiered_budget'
          ))
        );
        CREATE TABLE defense.v022_timing_policy_version (
          timing_policy_version_id uuid PRIMARY KEY,
          timing_policy_variant_id uuid NOT NULL
            REFERENCES defense.v022_timing_policy_variant,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          version_number integer NOT NULL CHECK (version_number >= 1),
          implementation_key varchar(240) NOT NULL
            CHECK (btrim(implementation_key)<>''),
          research_status varchar(24) NOT NULL
            CHECK (research_status IN ('exploratory','parity','formal')),
          supported_frequencies jsonb NOT NULL,
          input_policy jsonb NOT NULL,
          rule jsonb NOT NULL,
          version_fingerprint varchar(64) NOT NULL
            CHECK (version_fingerprint ~ '^[0-9a-f]{64}$'),
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (timing_policy_variant_id,version_number),
          UNIQUE (timing_policy_version_id,artifact_id),
          UNIQUE (timing_policy_version_id,version_fingerprint),
          CHECK (jsonb_typeof(supported_frequencies)='array' AND
                 jsonb_array_length(supported_frequencies)>0),
          CHECK (jsonb_typeof(input_policy)='object' AND
                 input_policy<>'{}'::jsonb),
          CHECK (jsonb_typeof(rule)='object' AND rule<>'{}'::jsonb),
          CHECK (rule->>'rule_type' IN (
            'fixed_budget','moving_average_tiered_budget'
          )),
          CHECK (
            input_policy->>'missing_input_policy'='fail' AND
            input_policy->>'stale_input_policy'='fail' AND
            input_policy->>'decision_cutoff'='scheduled_session_close' AND
            input_policy->>'execution_policy'='next_common_session_raw_open'
          )
        );

        CREATE TABLE defense.v022_allocation_policy_family (
          allocation_policy_family_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          family_key varchar(180) NOT NULL UNIQUE CHECK (btrim(family_key)<>''),
          name varchar(240) NOT NULL CHECK (btrim(name)<>''),
          formula_identity text NOT NULL CHECK (btrim(formula_identity)<>''),
          research_hypothesis text NOT NULL CHECK (btrim(research_hypothesis)<>''),
          created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE TABLE defense.v022_allocation_policy_variant (
          allocation_policy_variant_id uuid PRIMARY KEY,
          allocation_policy_family_id uuid NOT NULL
            REFERENCES defense.v022_allocation_policy_family,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          variant_key varchar(220) NOT NULL UNIQUE CHECK (btrim(variant_key)<>''),
          asset_registry_catalog_version varchar(32) NOT NULL
            CHECK (btrim(asset_registry_catalog_version)<>''),
          asset_set_key varchar(180) NOT NULL CHECK (btrim(asset_set_key)<>''),
          members_document jsonb NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (allocation_policy_variant_id,allocation_policy_family_id),
          CHECK (jsonb_typeof(members_document)='array' AND
                 jsonb_array_length(members_document)>0)
        );
        CREATE TABLE defense.v022_allocation_policy_version (
          allocation_policy_version_id uuid PRIMARY KEY,
          allocation_policy_variant_id uuid NOT NULL
            REFERENCES defense.v022_allocation_policy_variant,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          version_number integer NOT NULL CHECK (version_number >= 1),
          implementation_key varchar(240) NOT NULL
            CHECK (btrim(implementation_key)<>''),
          research_status varchar(24) NOT NULL
            CHECK (research_status IN ('exploratory','parity','formal')),
          formal_eligible boolean NOT NULL,
          missing_member_policy varchar(24) NOT NULL
            CHECK (missing_member_policy='fail'),
          reserve_fallback_policy varchar(24) NOT NULL
            CHECK (reserve_fallback_policy='forbidden'),
          rebalance_policy varchar(24) NOT NULL
            CHECK (rebalance_policy='with_strategy'),
          asset_registry_release_id uuid NOT NULL
            REFERENCES catalog.asset_registry_release,
          asset_registry_artifact_id uuid NOT NULL REFERENCES lineage.artifact,
          asset_set_definition_id uuid NOT NULL
            REFERENCES catalog.asset_set_definition,
          reserve_return_model_version_id uuid NULL
            REFERENCES experiment.reserve_return_model_version,
          reserve_return_model_artifact_id uuid NULL REFERENCES lineage.artifact,
          member_count integer NOT NULL CHECK (member_count > 0),
          version_fingerprint varchar(64) NOT NULL
            CHECK (version_fingerprint ~ '^[0-9a-f]{64}$'),
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (allocation_policy_variant_id,version_number),
          UNIQUE (allocation_policy_version_id,artifact_id),
          UNIQUE (allocation_policy_version_id,version_fingerprint),
          CHECK ((reserve_return_model_version_id IS NULL)=
                 (reserve_return_model_artifact_id IS NULL)),
          CHECK (research_status<>'formal' OR formal_eligible)
        );
        CREATE TABLE defense.v022_allocation_policy_member (
          allocation_policy_version_id uuid NOT NULL
            REFERENCES defense.v022_allocation_policy_version,
          ordinal integer NOT NULL CHECK (ordinal >= 0),
          security_id uuid NOT NULL REFERENCES catalog.security,
          asset_key varchar(180) NOT NULL CHECK (btrim(asset_key)<>''),
          component_role varchar(24) NOT NULL
            CHECK (component_role IN ('defensive_asset','reserve')),
          sleeve_weight numeric(24,18) NOT NULL
            CHECK (sleeve_weight > 0 AND sleeve_weight <= 1),
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (allocation_policy_version_id,ordinal),
          UNIQUE (allocation_policy_version_id,security_id),
          UNIQUE (allocation_policy_version_id,asset_key),
          UNIQUE (allocation_policy_version_id,component_role,ordinal)
        );
        CREATE UNIQUE INDEX uq_v022_allocation_single_reserve
          ON defense.v022_allocation_policy_member (
            allocation_policy_version_id,component_role
          ) WHERE component_role='reserve';
        """
    )


def _create_package_tables() -> None:
    op.execute(
        """
        CREATE TABLE defense.v022_defense_package_policy_binding (
          defense_version_id uuid PRIMARY KEY REFERENCES defense.defense_version,
          timing_policy_version_id uuid NOT NULL,
          timing_policy_artifact_id uuid NOT NULL,
          allocation_policy_version_id uuid NOT NULL,
          allocation_policy_artifact_id uuid NOT NULL,
          asset_registry_release_id uuid NOT NULL
            REFERENCES catalog.asset_registry_release,
          asset_registry_artifact_id uuid NOT NULL REFERENCES lineage.artifact,
          allocation_asset_set_definition_id uuid NOT NULL
            REFERENCES catalog.asset_set_definition,
          reserve_return_model_version_id uuid NULL
            REFERENCES experiment.reserve_return_model_version,
          reserve_return_model_artifact_id uuid NULL REFERENCES lineage.artifact,
          research_status varchar(24) NOT NULL
            CHECK (research_status IN ('exploratory','parity','formal')),
          supported_asset_set_count integer NOT NULL
            CHECK (supported_asset_set_count > 0),
          created_at timestamptz NOT NULL DEFAULT now(),
          FOREIGN KEY (timing_policy_version_id,timing_policy_artifact_id)
            REFERENCES defense.v022_timing_policy_version (
              timing_policy_version_id,artifact_id
            ),
          FOREIGN KEY (allocation_policy_version_id,allocation_policy_artifact_id)
            REFERENCES defense.v022_allocation_policy_version (
              allocation_policy_version_id,artifact_id
            ),
          CHECK ((reserve_return_model_version_id IS NULL)=
                 (reserve_return_model_artifact_id IS NULL))
        );
        CREATE TABLE defense.v022_defense_package_supported_asset_set (
          defense_version_id uuid NOT NULL
            REFERENCES defense.v022_defense_package_policy_binding,
          ordinal integer NOT NULL CHECK (ordinal >= 0),
          asset_context_key varchar(180) NOT NULL
            CHECK (btrim(asset_context_key)<>''),
          asset_registry_release_id uuid NOT NULL
            REFERENCES catalog.asset_registry_release,
          asset_registry_artifact_id uuid NOT NULL REFERENCES lineage.artifact,
          asset_set_definition_id uuid NOT NULL
            REFERENCES catalog.asset_set_definition,
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (defense_version_id,ordinal),
          UNIQUE (defense_version_id,asset_context_key),
          UNIQUE (defense_version_id,asset_set_definition_id)
        );
        """
    )


def _create_execution_context_tables() -> None:
    op.execute(
        """
        CREATE TABLE defense.v022_compiled_defense_execution_context (
          compiled_defense_execution_context_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          compiled_execution_data_context_id uuid NOT NULL
            REFERENCES workspace.v022_compiled_execution_data_context,
          defense_version_id uuid NOT NULL,
          defense_package_artifact_id uuid NOT NULL REFERENCES lineage.artifact,
          timing_policy_version_id uuid NOT NULL,
          timing_policy_artifact_id uuid NOT NULL,
          allocation_policy_version_id uuid NOT NULL,
          allocation_policy_artifact_id uuid NOT NULL,
          asset_registry_release_id uuid NOT NULL
            REFERENCES catalog.asset_registry_release,
          asset_registry_artifact_id uuid NOT NULL REFERENCES lineage.artifact,
          allocation_asset_set_definition_id uuid NOT NULL
            REFERENCES catalog.asset_set_definition,
          reserve_return_model_version_id uuid NULL
            REFERENCES experiment.reserve_return_model_version,
          reserve_return_model_artifact_id uuid NULL REFERENCES lineage.artifact,
          contract_version varchar(40) NOT NULL
            CHECK (contract_version='v0.22.0'),
          resolved_input_binding_document jsonb NOT NULL,
          resolved_input_binding_fingerprint varchar(64) NOT NULL
            CHECK (resolved_input_binding_fingerprint ~ '^[0-9a-f]{64}$'),
          input_count integer NOT NULL CHECK (input_count > 0),
          context_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (context_fingerprint ~ '^[0-9a-f]{64}$'),
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (compiled_execution_data_context_id,defense_version_id),
          UNIQUE (compiled_defense_execution_context_id,defense_version_id),
          FOREIGN KEY (defense_version_id)
            REFERENCES defense.v022_defense_package_policy_binding,
          FOREIGN KEY (timing_policy_version_id,timing_policy_artifact_id)
            REFERENCES defense.v022_timing_policy_version (
              timing_policy_version_id,artifact_id
            ),
          FOREIGN KEY (allocation_policy_version_id,allocation_policy_artifact_id)
            REFERENCES defense.v022_allocation_policy_version (
              allocation_policy_version_id,artifact_id
            ),
          CHECK ((reserve_return_model_version_id IS NULL)=
                 (reserve_return_model_artifact_id IS NULL)),
          CHECK (jsonb_typeof(resolved_input_binding_document)='object' AND
                 resolved_input_binding_document<>'{}'::jsonb),
          CHECK (
            resolved_input_binding_fingerprint=
              strategy.v022_strategy_parameter_fingerprint(
                resolved_input_binding_document
              )
          )
        );
        CREATE TABLE defense.v022_compiled_defense_execution_data_input (
          compiled_defense_execution_context_id uuid NOT NULL
            REFERENCES defense.v022_compiled_defense_execution_context,
          ordinal integer NOT NULL CHECK (ordinal >= 0),
          input_key varchar(160) NOT NULL CHECK (btrim(input_key)<>''),
          input_role varchar(32) NOT NULL
            CHECK (input_role IN (
              'timing_reference','defensive_asset','reserve_accrual'
            )),
          allocation_member_ordinal integer NULL
            CHECK (allocation_member_ordinal IS NULL OR
                   allocation_member_ordinal >= 0),
          dataset_publication_id uuid NOT NULL REFERENCES data.dataset_publication,
          dataset_artifact_id uuid NOT NULL REFERENCES lineage.artifact,
          dataset_fingerprint varchar(64) NOT NULL
            CHECK (dataset_fingerprint ~ '^[0-9a-f]{64}$'),
          calendar_version_id uuid NULL REFERENCES catalog.calendar_version,
          calendar_artifact_id uuid NULL REFERENCES lineage.artifact,
          calendar_fingerprint varchar(64) NULL
            CHECK (calendar_fingerprint IS NULL OR
                   calendar_fingerprint ~ '^[0-9a-f]{64}$'),
          reserve_return_model_version_id uuid NULL
            REFERENCES experiment.reserve_return_model_version,
          reserve_return_model_artifact_id uuid NULL REFERENCES lineage.artifact,
          coverage_start date NOT NULL,
          coverage_end date NOT NULL,
          security_ids jsonb NOT NULL,
          binding_document jsonb NOT NULL,
          binding_fingerprint varchar(64) NOT NULL
            CHECK (binding_fingerprint ~ '^[0-9a-f]{64}$'),
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (compiled_defense_execution_context_id,ordinal),
          UNIQUE (compiled_defense_execution_context_id,input_key),
          UNIQUE (
            compiled_defense_execution_context_id,input_role,
            allocation_member_ordinal
          ),
          CHECK ((calendar_version_id IS NULL)=
                 (calendar_artifact_id IS NULL)),
          CHECK ((calendar_version_id IS NULL)=
                 (calendar_fingerprint IS NULL)),
          CHECK ((reserve_return_model_version_id IS NULL)=
                 (reserve_return_model_artifact_id IS NULL)),
          CHECK (coverage_start <= coverage_end),
          CHECK (jsonb_typeof(security_ids)='array'),
          CHECK (jsonb_typeof(binding_document)='object' AND
                 binding_document<>'{}'::jsonb),
          CHECK (
            binding_fingerprint=
              strategy.v022_strategy_parameter_fingerprint(binding_document)
          ),
          CHECK (
            (input_role='timing_reference' AND
             allocation_member_ordinal IS NULL AND
             jsonb_array_length(security_ids)=1 AND
             reserve_return_model_version_id IS NULL) OR
            (input_role='defensive_asset' AND
             allocation_member_ordinal IS NOT NULL AND
             jsonb_array_length(security_ids)=1 AND
             reserve_return_model_version_id IS NULL) OR
            (input_role='reserve_accrual' AND
             allocation_member_ordinal IS NOT NULL AND
             jsonb_array_length(security_ids)=0 AND
             reserve_return_model_version_id IS NOT NULL)
          )
        );
        """
    )


def _create_policy_identity_guards() -> None:
    _create_timing_identity_guards()
    _create_allocation_identity_guards()


def _create_timing_identity_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION defense.validate_v022_timing_policy_family()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE artifact_type_value varchar; artifact_key_value varchar;
                artifact_version_value integer;
        BEGIN
          PERFORM data.assert_artifact_draft(NEW.artifact_id);
          SELECT artifact_type,artifact_key,version_number
            INTO artifact_type_value,artifact_key_value,artifact_version_value
            FROM lineage.artifact WHERE artifact_id=NEW.artifact_id;
          IF artifact_type_value IS DISTINCT FROM 'v022_defense_timing_family' OR
             artifact_key_value IS DISTINCT FROM NEW.family_key OR
             artifact_version_value IS DISTINCT FROM 1 THEN
            RAISE EXCEPTION 'Defense Timing Family requires its exact v1 Artifact identity';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_timing_policy_family_validate
          BEFORE INSERT ON defense.v022_timing_policy_family
          FOR EACH ROW EXECUTE FUNCTION defense.validate_v022_timing_policy_family();

        CREATE FUNCTION defense.validate_v022_timing_policy_variant()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE artifact_type_value varchar; artifact_key_value varchar;
                artifact_version_value integer; family_artifact_id_value uuid;
                family_artifact_status_value varchar;
        BEGIN
          PERFORM data.assert_artifact_draft(NEW.artifact_id);
          SELECT artifact_type,artifact_key,version_number
            INTO artifact_type_value,artifact_key_value,artifact_version_value
            FROM lineage.artifact WHERE artifact_id=NEW.artifact_id;
          SELECT family.artifact_id,artifact.status
            INTO family_artifact_id_value,family_artifact_status_value
            FROM defense.v022_timing_policy_family family
            JOIN lineage.artifact artifact ON artifact.artifact_id=family.artifact_id
           WHERE family.timing_policy_family_id=NEW.timing_policy_family_id;
          IF artifact_type_value IS DISTINCT FROM 'v022_defense_timing_variant' OR
             artifact_key_value IS DISTINCT FROM NEW.variant_key OR
             artifact_version_value IS DISTINCT FROM 1 THEN
            RAISE EXCEPTION 'Defense Timing Variant requires its exact v1 Artifact identity';
          END IF;
          IF family_artifact_status_value IS DISTINCT FROM 'published' OR
             NOT EXISTS (
               SELECT 1 FROM lineage.artifact_dependency dependency
                WHERE dependency.artifact_id=NEW.artifact_id
                  AND dependency.depends_on_artifact_id=family_artifact_id_value
                  AND dependency.role='defense_timing_family'
                  AND dependency.ordinal=0
             ) THEN
            RAISE EXCEPTION 'Defense Timing Variant requires exact published Family lineage';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_timing_policy_variant_validate
          BEFORE INSERT ON defense.v022_timing_policy_variant
          FOR EACH ROW EXECUTE FUNCTION defense.validate_v022_timing_policy_variant();

        CREATE FUNCTION defense.validate_v022_timing_policy_version()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE artifact_type_value varchar; artifact_key_value varchar;
                artifact_version_value integer; variant_key_value varchar;
                variant_rule_value jsonb; variant_artifact_id_value uuid;
                variant_artifact_status_value varchar;
        BEGIN
          PERFORM data.assert_artifact_draft(NEW.artifact_id);
          SELECT artifact_type,artifact_key,version_number
            INTO artifact_type_value,artifact_key_value,artifact_version_value
            FROM lineage.artifact WHERE artifact_id=NEW.artifact_id;
          SELECT variant.variant_key,variant.rule,variant.artifact_id,artifact.status
            INTO variant_key_value,variant_rule_value,variant_artifact_id_value,
                 variant_artifact_status_value
            FROM defense.v022_timing_policy_variant variant
            JOIN lineage.artifact artifact ON artifact.artifact_id=variant.artifact_id
           WHERE variant.timing_policy_variant_id=NEW.timing_policy_variant_id;
          IF artifact_type_value IS DISTINCT FROM 'v022_defense_timing_version' OR
             artifact_key_value IS DISTINCT FROM variant_key_value OR
             artifact_version_value IS DISTINCT FROM NEW.version_number THEN
            RAISE EXCEPTION 'Defense Timing Version requires its exact Artifact identity';
          END IF;
          IF variant_artifact_status_value IS DISTINCT FROM 'published' OR
             NEW.rule IS DISTINCT FROM variant_rule_value OR
             NOT EXISTS (
               SELECT 1 FROM lineage.artifact_dependency dependency
                WHERE dependency.artifact_id=NEW.artifact_id
                  AND dependency.depends_on_artifact_id=variant_artifact_id_value
                  AND dependency.role='defense_timing_variant'
                  AND dependency.ordinal=0
             ) THEN
            RAISE EXCEPTION 'Defense Timing Version requires exact published Variant lineage';
          END IF;
          IF (NEW.rule->>'rule_type'='fixed_budget' AND (
                (NEW.input_policy->>'market_timing_signal_required')::boolean
                  IS DISTINCT FROM false OR
                (NEW.input_policy->>'known_at_required')::boolean
                  IS DISTINCT FROM false
              )) OR
             (NEW.rule->>'rule_type'='moving_average_tiered_budget' AND (
                NEW.rule->>'price_field' IS DISTINCT FROM 'adjusted_close' OR
                (NEW.input_policy->>'market_timing_signal_required')::boolean
                  IS DISTINCT FROM true OR
                (NEW.input_policy->>'known_at_required')::boolean
                  IS DISTINCT FROM true
              )) THEN
            RAISE EXCEPTION 'Defense Timing rule and input policy are inconsistent';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_timing_policy_version_validate
          BEFORE INSERT ON defense.v022_timing_policy_version
          FOR EACH ROW EXECUTE FUNCTION defense.validate_v022_timing_policy_version();
        """
    )


def _create_allocation_identity_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION defense.validate_v022_allocation_policy_family()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE artifact_type_value varchar; artifact_key_value varchar;
                artifact_version_value integer;
        BEGIN
          PERFORM data.assert_artifact_draft(NEW.artifact_id);
          SELECT artifact_type,artifact_key,version_number
            INTO artifact_type_value,artifact_key_value,artifact_version_value
            FROM lineage.artifact WHERE artifact_id=NEW.artifact_id;
          IF artifact_type_value IS DISTINCT FROM 'v022_defense_allocation_family' OR
             artifact_key_value IS DISTINCT FROM NEW.family_key OR
             artifact_version_value IS DISTINCT FROM 1 THEN
            RAISE EXCEPTION 'Defense Allocation Family requires its exact v1 Artifact identity';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_allocation_policy_family_validate
          BEFORE INSERT ON defense.v022_allocation_policy_family
          FOR EACH ROW EXECUTE FUNCTION defense.validate_v022_allocation_policy_family();

        CREATE FUNCTION defense.validate_v022_allocation_policy_variant()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE artifact_type_value varchar; artifact_key_value varchar;
                artifact_version_value integer; family_artifact_id_value uuid;
                family_artifact_status_value varchar;
        BEGIN
          PERFORM data.assert_artifact_draft(NEW.artifact_id);
          SELECT artifact_type,artifact_key,version_number
            INTO artifact_type_value,artifact_key_value,artifact_version_value
            FROM lineage.artifact WHERE artifact_id=NEW.artifact_id;
          SELECT family.artifact_id,artifact.status
            INTO family_artifact_id_value,family_artifact_status_value
            FROM defense.v022_allocation_policy_family family
            JOIN lineage.artifact artifact ON artifact.artifact_id=family.artifact_id
           WHERE family.allocation_policy_family_id=NEW.allocation_policy_family_id;
          IF artifact_type_value IS DISTINCT FROM 'v022_defense_allocation_variant' OR
             artifact_key_value IS DISTINCT FROM NEW.variant_key OR
             artifact_version_value IS DISTINCT FROM 1 THEN
            RAISE EXCEPTION 'Defense Allocation Variant requires its exact v1 Artifact identity';
          END IF;
          IF family_artifact_status_value IS DISTINCT FROM 'published' OR
             NOT EXISTS (
               SELECT 1 FROM lineage.artifact_dependency dependency
                WHERE dependency.artifact_id=NEW.artifact_id
                  AND dependency.depends_on_artifact_id=family_artifact_id_value
                  AND dependency.role='defense_allocation_family'
                  AND dependency.ordinal=0
             ) THEN
            RAISE EXCEPTION 'Defense Allocation Variant requires exact published Family lineage';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_allocation_policy_variant_validate
          BEFORE INSERT ON defense.v022_allocation_policy_variant
          FOR EACH ROW EXECUTE FUNCTION defense.validate_v022_allocation_policy_variant();

        CREATE FUNCTION defense.validate_v022_allocation_policy_version()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE artifact_type_value varchar; artifact_key_value varchar;
                artifact_version_value integer; variant_key_value varchar;
                catalog_version_value varchar; asset_set_key_value varchar;
                variant_member_count_value integer;
                variant_artifact_id_value uuid; variant_artifact_status_value varchar;
                registry_artifact_id_value uuid; registry_artifact_status_value varchar;
                asset_set_release_id_value uuid; resolved_asset_set_key varchar;
                resolved_set_type varchar; model_artifact_id_value uuid;
                model_artifact_type_value varchar; model_artifact_status_value varchar;
                model_key_value varchar; model_version_value integer;
        BEGIN
          PERFORM data.assert_artifact_draft(NEW.artifact_id);
          SELECT artifact_type,artifact_key,version_number
            INTO artifact_type_value,artifact_key_value,artifact_version_value
            FROM lineage.artifact WHERE artifact_id=NEW.artifact_id;
          SELECT variant.variant_key,variant.asset_registry_catalog_version,
                 variant.asset_set_key,
                 jsonb_array_length(variant.members_document),
                 variant.artifact_id,artifact.status
            INTO variant_key_value,catalog_version_value,asset_set_key_value,
                 variant_member_count_value,variant_artifact_id_value,
                 variant_artifact_status_value
            FROM defense.v022_allocation_policy_variant variant
            JOIN lineage.artifact artifact ON artifact.artifact_id=variant.artifact_id
           WHERE variant.allocation_policy_variant_id=NEW.allocation_policy_variant_id;
          SELECT release.artifact_id,artifact.status
            INTO registry_artifact_id_value,registry_artifact_status_value
            FROM catalog.asset_registry_release release
            JOIN lineage.artifact artifact ON artifact.artifact_id=release.artifact_id
           WHERE release.asset_registry_release_id=NEW.asset_registry_release_id
             AND release.catalog_version=catalog_version_value;
          SELECT definition.asset_registry_release_id,definition.set_key,
                 definition.set_type
            INTO asset_set_release_id_value,resolved_asset_set_key,resolved_set_type
            FROM catalog.asset_set_definition definition
           WHERE definition.asset_set_definition_id=NEW.asset_set_definition_id;
          IF NEW.reserve_return_model_version_id IS NOT NULL THEN
            SELECT version.artifact_id,artifact.artifact_type,artifact.status,
                   definition.model_key,version.version_number
              INTO model_artifact_id_value,model_artifact_type_value,
                   model_artifact_status_value,model_key_value,model_version_value
              FROM experiment.reserve_return_model_version version
              JOIN experiment.reserve_return_model_definition definition
                ON definition.reserve_return_model_definition_id=
                   version.reserve_return_model_definition_id
              JOIN lineage.artifact artifact ON artifact.artifact_id=version.artifact_id
             WHERE version.reserve_return_model_version_id=
                   NEW.reserve_return_model_version_id;
          END IF;
          IF artifact_type_value IS DISTINCT FROM 'v022_defense_allocation_version' OR
             artifact_key_value IS DISTINCT FROM variant_key_value OR
             artifact_version_value IS DISTINCT FROM NEW.version_number THEN
            RAISE EXCEPTION 'Defense Allocation Version requires its exact Artifact identity';
          END IF;
          IF variant_artifact_status_value IS DISTINCT FROM 'published' OR
             NEW.member_count IS DISTINCT FROM variant_member_count_value OR
             registry_artifact_id_value IS DISTINCT FROM NEW.asset_registry_artifact_id OR
             registry_artifact_status_value IS DISTINCT FROM 'published' OR
             asset_set_release_id_value IS DISTINCT FROM NEW.asset_registry_release_id OR
             resolved_asset_set_key IS DISTINCT FROM asset_set_key_value OR
             resolved_set_type IS DISTINCT FROM 'defensive_basket' THEN
            RAISE EXCEPTION 'Defense Allocation Version requires exact published basket identities';
          END IF;
          IF NEW.reserve_return_model_version_id IS NOT NULL AND (
               model_artifact_id_value IS DISTINCT FROM NEW.reserve_return_model_artifact_id OR
               model_artifact_type_value IS DISTINCT FROM 'reserve_return_model_version' OR
               model_artifact_status_value IS DISTINCT FROM 'published' OR
               model_key_value IS DISTINCT FROM 'dgs3mo_cash_accrual_proxy' OR
               model_version_value IS DISTINCT FROM 1
             ) THEN
            RAISE EXCEPTION 'Defense Allocation Version requires the exact published Reserve Return Model';
          END IF;
          IF NOT EXISTS (
               SELECT 1 FROM lineage.artifact_dependency dependency
                WHERE dependency.artifact_id=NEW.artifact_id
                  AND dependency.depends_on_artifact_id=variant_artifact_id_value
                  AND dependency.role='defense_allocation_variant'
                  AND dependency.ordinal=0
             ) OR NOT EXISTS (
               SELECT 1 FROM lineage.artifact_dependency dependency
                WHERE dependency.artifact_id=NEW.artifact_id
                  AND dependency.depends_on_artifact_id=NEW.asset_registry_artifact_id
                  AND dependency.role='asset_registry_release'
                  AND dependency.ordinal=1
             ) OR (
               NEW.reserve_return_model_artifact_id IS NOT NULL AND NOT EXISTS (
                 SELECT 1 FROM lineage.artifact_dependency dependency
                  WHERE dependency.artifact_id=NEW.artifact_id
                    AND dependency.depends_on_artifact_id=
                        NEW.reserve_return_model_artifact_id
                    AND dependency.role='reserve_return_model_version'
                    AND dependency.ordinal=2
               )
             ) THEN
            RAISE EXCEPTION 'Defense Allocation Version lineage is incomplete';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_allocation_policy_version_validate
          BEFORE INSERT ON defense.v022_allocation_policy_version
          FOR EACH ROW EXECUTE FUNCTION defense.validate_v022_allocation_policy_version();

        CREATE FUNCTION defense.validate_v022_allocation_policy_member()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE expected_member jsonb; expected_security_id uuid;
                expected_security_key varchar; expected_set_member uuid;
        BEGIN
          SELECT variant.members_document->NEW.ordinal,
                 security.security_id,security.security_key,member.security_id
            INTO expected_member,expected_security_id,expected_security_key,
                 expected_set_member
            FROM defense.v022_allocation_policy_version version
            JOIN defense.v022_allocation_policy_variant variant
              ON variant.allocation_policy_variant_id=
                 version.allocation_policy_variant_id
            LEFT JOIN catalog.security security
              ON security.security_id=NEW.security_id
            LEFT JOIN catalog.asset_set_member member
              ON member.asset_set_definition_id=version.asset_set_definition_id
             AND member.security_id=NEW.security_id
           WHERE version.allocation_policy_version_id=
                 NEW.allocation_policy_version_id;
          IF expected_member IS NULL OR
             expected_security_id IS DISTINCT FROM NEW.security_id OR
             expected_security_key IS DISTINCT FROM NEW.asset_key OR
             expected_set_member IS NULL OR
             expected_member IS DISTINCT FROM jsonb_build_object(
               'ordinal',NEW.ordinal,
               'asset_key',NEW.asset_key,
               'component_role',NEW.component_role,
               'sleeve_weight',to_char(NEW.sleeve_weight,'FM0.000000000000000000')
             ) THEN
            RAISE EXCEPTION 'Defense Allocation member differs from its exact ordered basket contract';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_allocation_policy_member_validate
          BEFORE INSERT ON defense.v022_allocation_policy_member
          FOR EACH ROW EXECUTE FUNCTION defense.validate_v022_allocation_policy_member();
        """
    )


def _create_policy_completeness_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION defense.validate_v022_policy_identity_complete()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE artifact_status_value varchar; artifact_type_value varchar;
                dependency_count_value integer;
        BEGIN
          SELECT status,artifact_type INTO artifact_status_value,artifact_type_value
            FROM lineage.artifact WHERE artifact_id=NEW.artifact_id;
          SELECT count(*) INTO dependency_count_value
            FROM lineage.artifact_dependency
           WHERE artifact_id=NEW.artifact_id;
          IF artifact_status_value IS DISTINCT FROM 'published' OR
             artifact_type_value IS DISTINCT FROM TG_ARGV[0] OR
             dependency_count_value<>TG_ARGV[1]::integer THEN
            RAISE EXCEPTION 'Defense Policy Artifact identity or lineage is incomplete';
          END IF;
          RETURN NEW;
        END $$;
        CREATE CONSTRAINT TRIGGER trg_v022_timing_family_complete
          AFTER INSERT ON defense.v022_timing_policy_family
          DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
          EXECUTE FUNCTION defense.validate_v022_policy_identity_complete(
            'v022_defense_timing_family','0'
          );
        CREATE CONSTRAINT TRIGGER trg_v022_timing_variant_complete
          AFTER INSERT ON defense.v022_timing_policy_variant
          DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
          EXECUTE FUNCTION defense.validate_v022_policy_identity_complete(
            'v022_defense_timing_variant','1'
          );
        CREATE CONSTRAINT TRIGGER trg_v022_timing_version_complete
          AFTER INSERT ON defense.v022_timing_policy_version
          DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
          EXECUTE FUNCTION defense.validate_v022_policy_identity_complete(
            'v022_defense_timing_version','1'
          );
        CREATE CONSTRAINT TRIGGER trg_v022_allocation_family_complete
          AFTER INSERT ON defense.v022_allocation_policy_family
          DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
          EXECUTE FUNCTION defense.validate_v022_policy_identity_complete(
            'v022_defense_allocation_family','0'
          );
        CREATE CONSTRAINT TRIGGER trg_v022_allocation_variant_complete
          AFTER INSERT ON defense.v022_allocation_policy_variant
          DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
          EXECUTE FUNCTION defense.validate_v022_policy_identity_complete(
            'v022_defense_allocation_variant','1'
          );

        CREATE FUNCTION defense.validate_v022_allocation_policy_version_complete()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE artifact_status_value varchar; artifact_type_value varchar;
                artifact_fingerprint_value varchar; actual_member_count integer;
                actual_weight numeric(30,18); reserve_member_count integer;
                dependency_count_value integer; expected_dependency_count integer;
        BEGIN
          SELECT status,artifact_type,semantic_fingerprint
            INTO artifact_status_value,artifact_type_value,artifact_fingerprint_value
            FROM lineage.artifact WHERE artifact_id=NEW.artifact_id;
          SELECT count(*),coalesce(sum(sleeve_weight),0),
                 count(*) FILTER (WHERE component_role='reserve')
            INTO actual_member_count,actual_weight,reserve_member_count
            FROM defense.v022_allocation_policy_member
           WHERE allocation_policy_version_id=NEW.allocation_policy_version_id;
          SELECT count(*) INTO dependency_count_value
            FROM lineage.artifact_dependency
           WHERE artifact_id=NEW.artifact_id;
          expected_dependency_count :=
            CASE WHEN NEW.reserve_return_model_artifact_id IS NULL THEN 2 ELSE 3 END;
          IF artifact_status_value IS DISTINCT FROM 'published' OR
             artifact_type_value IS DISTINCT FROM
               'v022_defense_allocation_version' OR
             artifact_fingerprint_value IS NULL OR
             actual_member_count<>NEW.member_count OR
             actual_weight<>1::numeric OR
             reserve_member_count<>(
               CASE WHEN NEW.reserve_return_model_artifact_id IS NULL THEN 0 ELSE 1 END
             ) OR dependency_count_value<>expected_dependency_count THEN
            RAISE EXCEPTION 'Defense Allocation Version projection or lineage is incomplete';
          END IF;
          RETURN NEW;
        END $$;
        CREATE CONSTRAINT TRIGGER trg_v022_allocation_version_complete
          AFTER INSERT ON defense.v022_allocation_policy_version
          DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
          EXECUTE FUNCTION defense.validate_v022_allocation_policy_version_complete();
        """
    )


def _create_package_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION defense.validate_v022_defense_package_policy_binding()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE package_artifact_id_value uuid; package_artifact_status_value varchar;
                package_artifact_type_value varchar; timing_status_value varchar;
                allocation_status_value varchar; allocation_registry_id uuid;
                allocation_registry_artifact_id uuid; allocation_set_id uuid;
                allocation_model_id uuid; allocation_model_artifact_id uuid;
                declared_supported_set_count integer;
        BEGIN
          SELECT version.artifact_id,artifact.status,artifact.artifact_type,
                 jsonb_array_length(version.supported_asset_context_keys)
            INTO package_artifact_id_value,package_artifact_status_value,
                 package_artifact_type_value,declared_supported_set_count
            FROM defense.defense_version version
            JOIN lineage.artifact artifact ON artifact.artifact_id=version.artifact_id
           WHERE version.defense_version_id=NEW.defense_version_id;
          PERFORM data.assert_artifact_draft(package_artifact_id_value);
          SELECT artifact.status INTO timing_status_value
            FROM defense.v022_timing_policy_version version
            JOIN lineage.artifact artifact ON artifact.artifact_id=version.artifact_id
           WHERE version.timing_policy_version_id=NEW.timing_policy_version_id
             AND version.artifact_id=NEW.timing_policy_artifact_id;
          SELECT artifact.status,version.asset_registry_release_id,
                 version.asset_registry_artifact_id,version.asset_set_definition_id,
                 version.reserve_return_model_version_id,
                 version.reserve_return_model_artifact_id
            INTO allocation_status_value,allocation_registry_id,
                 allocation_registry_artifact_id,allocation_set_id,
                 allocation_model_id,allocation_model_artifact_id
            FROM defense.v022_allocation_policy_version version
            JOIN lineage.artifact artifact ON artifact.artifact_id=version.artifact_id
           WHERE version.allocation_policy_version_id=
                 NEW.allocation_policy_version_id
             AND version.artifact_id=NEW.allocation_policy_artifact_id;
          IF package_artifact_type_value IS DISTINCT FROM 'v022_defense_version' OR
             NEW.supported_asset_set_count IS DISTINCT FROM
               declared_supported_set_count OR
             timing_status_value IS DISTINCT FROM 'published' OR
             allocation_status_value IS DISTINCT FROM 'published' THEN
            RAISE EXCEPTION 'Defense Package requires exact published policy identities';
          END IF;
          IF allocation_registry_id IS DISTINCT FROM NEW.asset_registry_release_id OR
             allocation_registry_artifact_id IS DISTINCT FROM NEW.asset_registry_artifact_id OR
             allocation_set_id IS DISTINCT FROM NEW.allocation_asset_set_definition_id OR
             allocation_model_id IS DISTINCT FROM NEW.reserve_return_model_version_id OR
             allocation_model_artifact_id IS DISTINCT FROM NEW.reserve_return_model_artifact_id THEN
            RAISE EXCEPTION 'Defense Package does not reproduce its Allocation identities';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_defense_package_policy_binding_validate
          BEFORE INSERT ON defense.v022_defense_package_policy_binding
          FOR EACH ROW
          EXECUTE FUNCTION defense.validate_v022_defense_package_policy_binding();

        CREATE FUNCTION defense.validate_v022_defense_package_supported_asset_set()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE expected_key varchar; package_registry_id uuid;
                package_registry_artifact_id uuid; resolved_registry_id uuid;
                resolved_registry_artifact_id uuid; resolved_key varchar;
        BEGIN
          SELECT version.supported_asset_context_keys->>NEW.ordinal,
                 binding.asset_registry_release_id,binding.asset_registry_artifact_id
            INTO expected_key,package_registry_id,package_registry_artifact_id
            FROM defense.v022_defense_package_policy_binding binding
            JOIN defense.defense_version version
              ON version.defense_version_id=binding.defense_version_id
           WHERE binding.defense_version_id=NEW.defense_version_id;
          SELECT definition.asset_registry_release_id,release.artifact_id,
                 definition.set_key
            INTO resolved_registry_id,resolved_registry_artifact_id,resolved_key
            FROM catalog.asset_set_definition definition
            JOIN catalog.asset_registry_release release
              ON release.asset_registry_release_id=
                 definition.asset_registry_release_id
            JOIN lineage.artifact artifact ON artifact.artifact_id=release.artifact_id
           WHERE definition.asset_set_definition_id=NEW.asset_set_definition_id
             AND artifact.status='published';
          IF expected_key IS NULL OR NEW.asset_context_key IS DISTINCT FROM expected_key OR
             resolved_key IS DISTINCT FROM NEW.asset_context_key OR
             resolved_registry_id IS DISTINCT FROM NEW.asset_registry_release_id OR
             resolved_registry_artifact_id IS DISTINCT FROM NEW.asset_registry_artifact_id OR
             package_registry_id IS DISTINCT FROM NEW.asset_registry_release_id OR
             package_registry_artifact_id IS DISTINCT FROM NEW.asset_registry_artifact_id THEN
            RAISE EXCEPTION 'Defense Package supported Asset Set identity is not exact';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_defense_package_supported_asset_set_validate
          BEFORE INSERT ON defense.v022_defense_package_supported_asset_set
          FOR EACH ROW
          EXECUTE FUNCTION defense.validate_v022_defense_package_supported_asset_set();

        CREATE FUNCTION defense.validate_v022_defense_package_complete()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE package_artifact_id_value uuid; package_artifact_status_value varchar;
                actual_set_count integer; dependency_count_value integer;
                expected_dependency_count integer; variant_artifact_id_value uuid;
        BEGIN
          SELECT version.artifact_id,artifact.status,variant.artifact_id
            INTO package_artifact_id_value,package_artifact_status_value,
                 variant_artifact_id_value
            FROM defense.defense_version version
            JOIN defense.defense_variant variant
              ON variant.defense_variant_id=version.defense_variant_id
            JOIN lineage.artifact artifact ON artifact.artifact_id=version.artifact_id
           WHERE version.defense_version_id=NEW.defense_version_id;
          SELECT count(*) INTO actual_set_count
            FROM defense.v022_defense_package_supported_asset_set
           WHERE defense_version_id=NEW.defense_version_id;
          SELECT count(*) INTO dependency_count_value
            FROM lineage.artifact_dependency
           WHERE artifact_id=package_artifact_id_value;
          expected_dependency_count :=
            CASE WHEN NEW.reserve_return_model_artifact_id IS NULL THEN 4 ELSE 5 END;
          IF package_artifact_status_value IS DISTINCT FROM 'published' OR
             actual_set_count<>NEW.supported_asset_set_count OR
             dependency_count_value<>expected_dependency_count OR
             NOT EXISTS (
               SELECT 1 FROM lineage.artifact_dependency dependency
                WHERE dependency.artifact_id=package_artifact_id_value
                  AND dependency.depends_on_artifact_id=variant_artifact_id_value
                  AND dependency.role='defense_variant' AND dependency.ordinal=0
             ) OR NOT EXISTS (
               SELECT 1 FROM lineage.artifact_dependency dependency
                WHERE dependency.artifact_id=package_artifact_id_value
                  AND dependency.depends_on_artifact_id=NEW.timing_policy_artifact_id
                  AND dependency.role='defense_timing_policy_version'
                  AND dependency.ordinal=1
             ) OR NOT EXISTS (
               SELECT 1 FROM lineage.artifact_dependency dependency
                WHERE dependency.artifact_id=package_artifact_id_value
                  AND dependency.depends_on_artifact_id=NEW.allocation_policy_artifact_id
                  AND dependency.role='defense_allocation_policy_version'
                  AND dependency.ordinal=2
             ) OR NOT EXISTS (
               SELECT 1 FROM lineage.artifact_dependency dependency
                WHERE dependency.artifact_id=package_artifact_id_value
                  AND dependency.depends_on_artifact_id=NEW.asset_registry_artifact_id
                  AND dependency.role='asset_registry_release'
                  AND dependency.ordinal=3
             ) OR (
               NEW.reserve_return_model_artifact_id IS NOT NULL AND NOT EXISTS (
                 SELECT 1 FROM lineage.artifact_dependency dependency
                  WHERE dependency.artifact_id=package_artifact_id_value
                    AND dependency.depends_on_artifact_id=
                        NEW.reserve_return_model_artifact_id
                    AND dependency.role='reserve_return_model_version'
                    AND dependency.ordinal=4
               )
             ) THEN
            RAISE EXCEPTION 'Defense Package policy, Asset Set, or lineage projection is incomplete';
          END IF;
          RETURN NEW;
        END $$;
        CREATE CONSTRAINT TRIGGER trg_v022_defense_package_complete
          AFTER INSERT ON defense.v022_defense_package_policy_binding
          DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
          EXECUTE FUNCTION defense.validate_v022_defense_package_complete();
        """
    )


def _create_execution_context_guards() -> None:
    _create_execution_context_insert_guard()
    _create_execution_input_insert_guard()
    _create_execution_context_completeness_guard()


def _create_execution_context_insert_guard() -> None:
    op.execute(
        """
        CREATE FUNCTION defense.validate_v022_compiled_defense_execution_context()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE context_artifact_type_value varchar; context_artifact_key_value varchar;
                context_artifact_version_value integer; risk_fingerprint_value varchar;
                risk_artifact_id_value uuid; risk_artifact_status_value varchar;
                risk_registry_release_id_value uuid;
                risk_registry_artifact_id_value uuid;
                risk_asset_set_definition_id_value uuid;
                package_artifact_id_value uuid; package_artifact_status_value varchar;
                package_fingerprint_value varchar; binding record;
        BEGIN
          PERFORM data.assert_artifact_draft(NEW.artifact_id);
          SELECT artifact_type,artifact_key,version_number
            INTO context_artifact_type_value,context_artifact_key_value,
                 context_artifact_version_value
            FROM lineage.artifact WHERE artifact_id=NEW.artifact_id;
          SELECT context.context_fingerprint,context.artifact_id,artifact.status,
                 context.asset_registry_release_id,
                 context.asset_registry_artifact_id,
                 context.asset_set_definition_id
            INTO risk_fingerprint_value,risk_artifact_id_value,risk_artifact_status_value,
                 risk_registry_release_id_value,risk_registry_artifact_id_value,
                 risk_asset_set_definition_id_value
            FROM workspace.v022_compiled_execution_data_context context
            JOIN lineage.artifact artifact ON artifact.artifact_id=context.artifact_id
           WHERE context.compiled_execution_data_context_id=
                 NEW.compiled_execution_data_context_id;
          SELECT version.artifact_id,artifact.status,version.version_fingerprint
            INTO package_artifact_id_value,package_artifact_status_value,
                 package_fingerprint_value
            FROM defense.defense_version version
            JOIN lineage.artifact artifact ON artifact.artifact_id=version.artifact_id
           WHERE version.defense_version_id=NEW.defense_version_id;
          SELECT package.* INTO binding
            FROM defense.v022_defense_package_policy_binding package
           WHERE package.defense_version_id=NEW.defense_version_id;
          IF context_artifact_type_value IS DISTINCT FROM
               'v022_compiled_defense_execution_context' OR
             context_artifact_version_value IS DISTINCT FROM 1 OR
             context_artifact_key_value IS DISTINCT FROM
               'compiled_defense_execution_context__' || risk_fingerprint_value ||
               '__' || package_fingerprint_value THEN
            RAISE EXCEPTION 'Compiled Defense Execution Context requires its exact v1 Artifact identity';
          END IF;
          IF risk_artifact_status_value IS DISTINCT FROM 'published' OR
             package_artifact_status_value IS DISTINCT FROM 'published' OR
             package_artifact_id_value IS DISTINCT FROM
               NEW.defense_package_artifact_id THEN
            RAISE EXCEPTION 'Compiled Defense Execution Context requires exact published risk and Package identities';
          END IF;
          IF NOT EXISTS (
               SELECT 1
                 FROM defense.v022_defense_package_supported_asset_set supported
                WHERE supported.defense_version_id=NEW.defense_version_id
                  AND supported.asset_registry_release_id=
                      risk_registry_release_id_value
                  AND supported.asset_registry_artifact_id=
                      risk_registry_artifact_id_value
                  AND supported.asset_set_definition_id=
                      risk_asset_set_definition_id_value
             ) THEN
            RAISE EXCEPTION 'Compiled Defense Execution Context risk Asset Context is not supported by its Package';
          END IF;
          IF binding.timing_policy_version_id IS DISTINCT FROM
               NEW.timing_policy_version_id OR
             binding.timing_policy_artifact_id IS DISTINCT FROM
               NEW.timing_policy_artifact_id OR
             binding.allocation_policy_version_id IS DISTINCT FROM
               NEW.allocation_policy_version_id OR
             binding.allocation_policy_artifact_id IS DISTINCT FROM
               NEW.allocation_policy_artifact_id OR
             binding.asset_registry_release_id IS DISTINCT FROM
               NEW.asset_registry_release_id OR
             binding.asset_registry_artifact_id IS DISTINCT FROM
               NEW.asset_registry_artifact_id OR
             binding.allocation_asset_set_definition_id IS DISTINCT FROM
               NEW.allocation_asset_set_definition_id OR
             binding.reserve_return_model_version_id IS DISTINCT FROM
               NEW.reserve_return_model_version_id OR
             binding.reserve_return_model_artifact_id IS DISTINCT FROM
               NEW.reserve_return_model_artifact_id THEN
            RAISE EXCEPTION 'Compiled Defense Execution Context does not reproduce its exact Package identities';
          END IF;
          IF NEW.resolved_input_binding_document->>'contract_version' IS DISTINCT FROM
               'v0.22.0' OR
             jsonb_typeof(NEW.resolved_input_binding_document->'bindings')
               IS DISTINCT FROM 'array' OR
             jsonb_array_length(NEW.resolved_input_binding_document->'bindings')<>
               NEW.input_count THEN
            RAISE EXCEPTION 'Compiled Defense Execution Context requires an exact v0.22 binding document';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_compiled_defense_execution_context_validate
          BEFORE INSERT ON defense.v022_compiled_defense_execution_context
          FOR EACH ROW
          EXECUTE FUNCTION defense.validate_v022_compiled_defense_execution_context();
        """
    )


def _create_execution_input_insert_guard() -> None:
    op.execute(
        """
        CREATE FUNCTION defense.validate_v022_compiled_defense_execution_data_input()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE expected_binding jsonb; context_row record; dataset_row record;
                calendar_row record; member_row record; timing_rule jsonb;
                security_id_value uuid; security_key_value varchar;
        BEGIN
          SELECT context.*,
                 context.resolved_input_binding_document->'bindings'->NEW.ordinal
                   AS expected_binding
            INTO context_row
            FROM defense.v022_compiled_defense_execution_context context
           WHERE context.compiled_defense_execution_context_id=
                 NEW.compiled_defense_execution_context_id;
          expected_binding := context_row.expected_binding;
          SELECT publication.*,artifact.artifact_id AS exact_artifact_id,
                 artifact.artifact_type,artifact.status AS artifact_status,
                 artifact.semantic_fingerprint
            INTO dataset_row
            FROM data.dataset_publication publication
            JOIN lineage.artifact artifact ON artifact.artifact_id=publication.artifact_id
           WHERE publication.dataset_publication_id=NEW.dataset_publication_id;
          IF NEW.calendar_version_id IS NOT NULL THEN
            SELECT version.artifact_id,artifact.artifact_type,
                   artifact.status,artifact.semantic_fingerprint
              INTO calendar_row
              FROM catalog.calendar_version version
              JOIN lineage.artifact artifact ON artifact.artifact_id=version.artifact_id
             WHERE version.calendar_version_id=NEW.calendar_version_id;
          END IF;
          IF NEW.allocation_member_ordinal IS NOT NULL THEN
            SELECT member.* INTO member_row
              FROM defense.v022_allocation_policy_member member
             WHERE member.allocation_policy_version_id=
                   context_row.allocation_policy_version_id
               AND member.ordinal=NEW.allocation_member_ordinal;
          END IF;
          SELECT version.rule INTO timing_rule
            FROM defense.v022_timing_policy_version version
           WHERE version.timing_policy_version_id=
                 context_row.timing_policy_version_id;
          IF jsonb_array_length(NEW.security_ids)=1 THEN
            security_id_value := (NEW.security_ids->>0)::uuid;
            SELECT security_key INTO security_key_value
              FROM catalog.security WHERE security_id=security_id_value;
          END IF;
          IF expected_binding IS NULL OR
             NEW.binding_document IS DISTINCT FROM expected_binding OR
             NEW.input_key IS DISTINCT FROM expected_binding->>'input_key' OR
             NEW.input_role IS DISTINCT FROM expected_binding->>'input_role' OR
             NEW.allocation_member_ordinal IS DISTINCT FROM
               nullif(expected_binding->>'allocation_member_ordinal','')::integer OR
             NEW.dataset_publication_id IS DISTINCT FROM
               (expected_binding->>'dataset_publication_id')::uuid OR
             NEW.dataset_artifact_id IS DISTINCT FROM
               (expected_binding->>'dataset_artifact_id')::uuid OR
             NEW.dataset_fingerprint IS DISTINCT FROM
               expected_binding->>'dataset_fingerprint' OR
             dataset_row.dataset_key IS DISTINCT FROM
               expected_binding->>'dataset_key' OR
             dataset_row.version_number IS DISTINCT FROM
               (expected_binding->>'dataset_version_number')::integer OR
             NEW.calendar_version_id IS DISTINCT FROM
               nullif(expected_binding->>'calendar_version_id','')::uuid OR
             NEW.calendar_artifact_id IS DISTINCT FROM
               nullif(expected_binding->>'calendar_artifact_id','')::uuid OR
             NEW.calendar_fingerprint IS DISTINCT FROM
               nullif(expected_binding->>'calendar_fingerprint','') OR
             NEW.reserve_return_model_version_id IS DISTINCT FROM
               nullif(expected_binding->>'reserve_return_model_version_id','')::uuid OR
             NEW.reserve_return_model_artifact_id IS DISTINCT FROM
               nullif(expected_binding->>'reserve_return_model_artifact_id','')::uuid OR
             NEW.coverage_start IS DISTINCT FROM
               (expected_binding->>'coverage_start')::date OR
             NEW.coverage_end IS DISTINCT FROM
               (expected_binding->>'coverage_end')::date OR
             NEW.security_ids IS DISTINCT FROM expected_binding->'security_ids' THEN
            RAISE EXCEPTION 'Compiled Defense Data Input differs from its exact binding document';
          END IF;
          IF dataset_row.exact_artifact_id IS DISTINCT FROM NEW.dataset_artifact_id OR
             dataset_row.artifact_type IS DISTINCT FROM 'dataset_publication' OR
             dataset_row.artifact_status IS DISTINCT FROM 'published' OR
             dataset_row.semantic_fingerprint IS DISTINCT FROM NEW.dataset_fingerprint OR
             dataset_row.coverage_start IS DISTINCT FROM NEW.coverage_start OR
             dataset_row.coverage_end IS DISTINCT FROM NEW.coverage_end OR
             dataset_row.calendar_version_id IS DISTINCT FROM NEW.calendar_version_id THEN
            RAISE EXCEPTION 'Compiled Defense Data Input requires its exact published Dataset';
          END IF;
          IF NEW.calendar_version_id IS NOT NULL AND (
               calendar_row.artifact_id IS DISTINCT FROM NEW.calendar_artifact_id OR
               calendar_row.artifact_type IS DISTINCT FROM 'calendar_version' OR
               calendar_row.status IS DISTINCT FROM 'published' OR
               calendar_row.semantic_fingerprint IS DISTINCT FROM NEW.calendar_fingerprint
             ) THEN
            RAISE EXCEPTION 'Compiled Defense Data Input requires its exact published Calendar';
          END IF;
          IF NEW.input_role='timing_reference' AND (
               timing_rule->>'rule_type' IS DISTINCT FROM
                 'moving_average_tiered_budget' OR
               dataset_row.value_kind IS DISTINCT FROM 'daily_bar' OR
               security_key_value IS DISTINCT FROM
                 timing_rule->>'reference_asset_key'
             ) THEN
            RAISE EXCEPTION 'Timing reference input does not match the Timing Policy';
          END IF;
          IF NEW.input_role='defensive_asset' AND (
               member_row.component_role IS DISTINCT FROM 'defensive_asset' OR
               dataset_row.value_kind IS DISTINCT FROM 'daily_bar' OR
               member_row.security_id IS DISTINCT FROM security_id_value
             ) THEN
            RAISE EXCEPTION 'Defensive asset input does not match its Allocation member';
          END IF;
          IF NEW.input_role='reserve_accrual' AND (
               member_row.component_role IS DISTINCT FROM 'reserve' OR
               NEW.reserve_return_model_version_id IS DISTINCT FROM
                 context_row.reserve_return_model_version_id OR
               NEW.reserve_return_model_artifact_id IS DISTINCT FROM
                 context_row.reserve_return_model_artifact_id OR
               dataset_row.value_kind IS DISTINCT FROM 'reserve_return' OR
               NOT EXISTS (
                 SELECT 1 FROM lineage.artifact_dependency dependency
                  WHERE dependency.artifact_id=NEW.dataset_artifact_id
                    AND dependency.depends_on_artifact_id=
                        NEW.reserve_return_model_artifact_id
                    AND dependency.role='reserve_model'
               )
             ) THEN
            RAISE EXCEPTION 'Reserve accrual input does not match its Allocation and Reserve Model';
          END IF;
          IF NEW.input_role IN ('timing_reference','defensive_asset') AND
             NOT EXISTS (
               SELECT 1 FROM catalog.security security
               JOIN data.dataset_coverage coverage
                 ON coverage.asset_id=security.legacy_asset_id
              WHERE security.security_id=security_id_value
                AND coverage.dataset_publication_id=NEW.dataset_publication_id
             ) THEN
            RAISE EXCEPTION 'Compiled Defense Data Input lacks exact Security coverage';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_compiled_defense_execution_data_input_validate
          BEFORE INSERT ON defense.v022_compiled_defense_execution_data_input
          FOR EACH ROW
          EXECUTE FUNCTION defense.validate_v022_compiled_defense_execution_data_input();
        """
    )


def _create_execution_context_completeness_guard() -> None:
    op.execute(
        """
        CREATE FUNCTION defense.validate_v022_compiled_defense_execution_context_complete()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE artifact_status_value varchar; artifact_type_value varchar;
                artifact_fingerprint_value varchar; actual_input_count integer;
                expected_binding_document jsonb; timing_rule_type varchar;
                timing_input_count integer; defensive_input_count integer;
                reserve_input_count integer; expected_defensive_count integer;
                expected_reserve_count integer; dependency_count_value integer;
                expected_dependency_count integer; risk_artifact_id_value uuid;
                risk_coverage_start_value date; risk_coverage_end_value date;
        BEGIN
          SELECT status,artifact_type,semantic_fingerprint
            INTO artifact_status_value,artifact_type_value,artifact_fingerprint_value
            FROM lineage.artifact WHERE artifact_id=NEW.artifact_id;
          SELECT context.artifact_id,max(input.coverage_start),min(input.coverage_end)
            INTO risk_artifact_id_value,risk_coverage_start_value,
                 risk_coverage_end_value
            FROM workspace.v022_compiled_execution_data_context context
            JOIN workspace.v022_compiled_execution_data_input input
              ON input.compiled_execution_data_context_id=
                 context.compiled_execution_data_context_id
           WHERE context.compiled_execution_data_context_id=
                 NEW.compiled_execution_data_context_id
           GROUP BY context.artifact_id;
          SELECT rule->>'rule_type' INTO timing_rule_type
            FROM defense.v022_timing_policy_version
           WHERE timing_policy_version_id=NEW.timing_policy_version_id;
          SELECT count(*),
                 count(*) FILTER (WHERE input_role='timing_reference'),
                 count(*) FILTER (WHERE input_role='defensive_asset'),
                 count(*) FILTER (WHERE input_role='reserve_accrual'),
                 jsonb_build_object(
                   'contract_version','v0.22.0',
                   'bindings',coalesce(
                     jsonb_agg(input.binding_document ORDER BY input.ordinal),
                     '[]'::jsonb
                   )
                 )
            INTO actual_input_count,timing_input_count,defensive_input_count,
                 reserve_input_count,expected_binding_document
            FROM defense.v022_compiled_defense_execution_data_input input
           WHERE input.compiled_defense_execution_context_id=
                 NEW.compiled_defense_execution_context_id;
          SELECT count(*) FILTER (WHERE component_role='defensive_asset'),
                 count(*) FILTER (WHERE component_role='reserve')
            INTO expected_defensive_count,expected_reserve_count
            FROM defense.v022_allocation_policy_member
           WHERE allocation_policy_version_id=NEW.allocation_policy_version_id;
          SELECT count(*) INTO dependency_count_value
            FROM lineage.artifact_dependency
           WHERE artifact_id=NEW.artifact_id;
          SELECT 5 +
                 CASE WHEN NEW.reserve_return_model_artifact_id IS NULL THEN 0 ELSE 1 END +
                 count(DISTINCT input.dataset_artifact_id) +
                 count(DISTINCT input.calendar_artifact_id)
            INTO expected_dependency_count
            FROM defense.v022_compiled_defense_execution_data_input input
           WHERE input.compiled_defense_execution_context_id=
                 NEW.compiled_defense_execution_context_id;
          IF artifact_status_value IS DISTINCT FROM 'published' OR
             artifact_type_value IS DISTINCT FROM
               'v022_compiled_defense_execution_context' OR
             artifact_fingerprint_value IS DISTINCT FROM NEW.context_fingerprint OR
             actual_input_count<>NEW.input_count OR
             NEW.resolved_input_binding_document IS DISTINCT FROM
               expected_binding_document OR
             timing_input_count<>(
               CASE WHEN timing_rule_type='fixed_budget' THEN 0 ELSE 1 END
             ) OR
             defensive_input_count<>expected_defensive_count OR
             reserve_input_count<>expected_reserve_count OR
             EXISTS (
               SELECT 1
                 FROM defense.v022_compiled_defense_execution_data_input input
                WHERE input.compiled_defense_execution_context_id=
                      NEW.compiled_defense_execution_context_id
                  AND (
                    input.coverage_start>risk_coverage_start_value OR
                    input.coverage_end<risk_coverage_end_value
                  )
             ) THEN
            RAISE EXCEPTION 'Compiled Defense Execution Context projection is incomplete';
          END IF;
          IF dependency_count_value<>expected_dependency_count OR
             NOT EXISTS (
               SELECT 1 FROM lineage.artifact_dependency dependency
                WHERE dependency.artifact_id=NEW.artifact_id
                  AND dependency.depends_on_artifact_id=risk_artifact_id_value
                  AND dependency.role='compiled_execution_data_context'
                  AND dependency.ordinal=0
             ) OR NOT EXISTS (
               SELECT 1 FROM lineage.artifact_dependency dependency
                WHERE dependency.artifact_id=NEW.artifact_id
                  AND dependency.depends_on_artifact_id=
                      NEW.defense_package_artifact_id
                  AND dependency.role='defense_package'
                  AND dependency.ordinal=1
             ) OR NOT EXISTS (
               SELECT 1 FROM lineage.artifact_dependency dependency
                WHERE dependency.artifact_id=NEW.artifact_id
                  AND dependency.depends_on_artifact_id=
                      NEW.timing_policy_artifact_id
                  AND dependency.role='defense_timing_policy_version'
                  AND dependency.ordinal=2
             ) OR NOT EXISTS (
               SELECT 1 FROM lineage.artifact_dependency dependency
                WHERE dependency.artifact_id=NEW.artifact_id
                  AND dependency.depends_on_artifact_id=
                      NEW.allocation_policy_artifact_id
                  AND dependency.role='defense_allocation_policy_version'
                  AND dependency.ordinal=3
             ) OR NOT EXISTS (
               SELECT 1 FROM lineage.artifact_dependency dependency
                WHERE dependency.artifact_id=NEW.artifact_id
                  AND dependency.depends_on_artifact_id=
                      NEW.asset_registry_artifact_id
                  AND dependency.role='asset_registry_release'
                  AND dependency.ordinal=4
             ) OR (
               NEW.reserve_return_model_artifact_id IS NOT NULL AND NOT EXISTS (
                 SELECT 1 FROM lineage.artifact_dependency dependency
                  WHERE dependency.artifact_id=NEW.artifact_id
                    AND dependency.depends_on_artifact_id=
                        NEW.reserve_return_model_artifact_id
                    AND dependency.role='reserve_return_model_version'
                    AND dependency.ordinal=5
               )
             ) OR EXISTS (
               SELECT 1 FROM (
                 SELECT dataset_artifact_id,min(ordinal) AS dependency_ordinal
                   FROM defense.v022_compiled_defense_execution_data_input
                  WHERE compiled_defense_execution_context_id=
                        NEW.compiled_defense_execution_context_id
                  GROUP BY dataset_artifact_id
               ) expected
               WHERE NOT EXISTS (
                 SELECT 1 FROM lineage.artifact_dependency dependency
                  WHERE dependency.artifact_id=NEW.artifact_id
                    AND dependency.depends_on_artifact_id=
                        expected.dataset_artifact_id
                    AND dependency.role='defense_data_input'
                    AND dependency.ordinal=expected.dependency_ordinal
               )
             ) OR EXISTS (
               SELECT 1 FROM (
                 SELECT calendar_artifact_id,min(ordinal) AS dependency_ordinal
                   FROM defense.v022_compiled_defense_execution_data_input
                  WHERE compiled_defense_execution_context_id=
                        NEW.compiled_defense_execution_context_id
                    AND calendar_artifact_id IS NOT NULL
                  GROUP BY calendar_artifact_id
               ) expected
               WHERE NOT EXISTS (
                 SELECT 1 FROM lineage.artifact_dependency dependency
                  WHERE dependency.artifact_id=NEW.artifact_id
                    AND dependency.depends_on_artifact_id=
                        expected.calendar_artifact_id
                    AND dependency.role='defense_calendar'
                    AND dependency.ordinal=expected.dependency_ordinal
               )
             ) THEN
            RAISE EXCEPTION 'Compiled Defense Execution Context lineage is not exact';
          END IF;
          RETURN NEW;
        END $$;
        CREATE CONSTRAINT TRIGGER trg_v022_compiled_defense_execution_context_complete
          AFTER INSERT ON defense.v022_compiled_defense_execution_context
          DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
          EXECUTE FUNCTION defense.validate_v022_compiled_defense_execution_context_complete();
        """
    )


def _create_append_only_guards() -> None:
    for table in (
        "v022_timing_policy_family",
        "v022_timing_policy_variant",
        "v022_timing_policy_version",
        "v022_allocation_policy_family",
        "v022_allocation_policy_variant",
        "v022_allocation_policy_version",
        "v022_allocation_policy_member",
        "v022_defense_package_policy_binding",
        "v022_defense_package_supported_asset_set",
        "v022_compiled_defense_execution_context",
        "v022_compiled_defense_execution_data_input",
    ):
        op.execute(
            f"CREATE TRIGGER trg_{table}_append_only BEFORE UPDATE OR DELETE "
            f"ON defense.{table} FOR EACH ROW "
            "EXECUTE FUNCTION lineage.reject_record_mutation()"
        )


def downgrade() -> None:
    for function in (
        "validate_v022_compiled_defense_execution_context_complete",
        "validate_v022_compiled_defense_execution_data_input",
        "validate_v022_compiled_defense_execution_context",
        "validate_v022_defense_package_complete",
        "validate_v022_defense_package_supported_asset_set",
        "validate_v022_defense_package_policy_binding",
        "validate_v022_allocation_policy_version_complete",
        "validate_v022_policy_identity_complete",
        "validate_v022_allocation_policy_member",
        "validate_v022_allocation_policy_version",
        "validate_v022_allocation_policy_variant",
        "validate_v022_allocation_policy_family",
        "validate_v022_timing_policy_version",
        "validate_v022_timing_policy_variant",
        "validate_v022_timing_policy_family",
    ):
        op.execute(f"DROP FUNCTION IF EXISTS defense.{function}() CASCADE")
    op.drop_table("v022_compiled_defense_execution_data_input", schema="defense")
    op.drop_table("v022_compiled_defense_execution_context", schema="defense")
    op.drop_table("v022_defense_package_supported_asset_set", schema="defense")
    op.drop_table("v022_defense_package_policy_binding", schema="defense")
    op.execute("DROP INDEX IF EXISTS defense.uq_v022_allocation_single_reserve")
    op.drop_table("v022_allocation_policy_member", schema="defense")
    op.drop_table("v022_allocation_policy_version", schema="defense")
    op.drop_table("v022_allocation_policy_variant", schema="defense")
    op.drop_table("v022_allocation_policy_family", schema="defense")
    op.drop_table("v022_timing_policy_version", schema="defense")
    op.drop_table("v022_timing_policy_variant", schema="defense")
    op.drop_table("v022_timing_policy_family", schema="defense")
    _set_release_component_kinds(include_defense_policies=False)
