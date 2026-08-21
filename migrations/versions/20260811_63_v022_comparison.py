# ruff: noqa: E501
"""Add v0.22 Comparison and matched-baseline identity.

Revision ID: 20260811_63_v022_comparison
Revises: 20260811_62_v022_exp_identity
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260811_63_v022_comparison"
down_revision: str | None = "20260811_62_v022_exp_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE experiment.v022_result_comparison (
          result_comparison_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          left_result_evidence_snapshot_id uuid NOT NULL
            REFERENCES experiment.v022_result_evidence_snapshot,
          right_result_evidence_snapshot_id uuid NOT NULL
            REFERENCES experiment.v022_result_evidence_snapshot,
          comparison_scope varchar(32) NOT NULL CHECK (
            comparison_scope IN ('predictive','portfolio','replication_audit')
          ),
          classification varchar(24) NOT NULL CHECK (
            classification IN ('replication','controlled','multi_axis','incompatible')
          ),
          left_protected_context_fingerprint varchar(64) NOT NULL
            CHECK (left_protected_context_fingerprint ~ '^[0-9a-f]{64}$'),
          right_protected_context_fingerprint varchar(64) NOT NULL
            CHECK (right_protected_context_fingerprint ~ '^[0-9a-f]{64}$'),
          changed_dimensions jsonb NOT NULL,
          comparison_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (comparison_fingerprint ~ '^[0-9a-f]{64}$'),
          comparison_document jsonb NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          CHECK (left_result_evidence_snapshot_id <> right_result_evidence_snapshot_id),
          UNIQUE (left_result_evidence_snapshot_id,right_result_evidence_snapshot_id,comparison_scope)
        );
        CREATE TABLE experiment.v022_matched_baseline_assessment (
          matched_baseline_assessment_id uuid PRIMARY KEY,
          artifact_id uuid NOT NULL UNIQUE REFERENCES lineage.artifact,
          subject_result_evidence_snapshot_id uuid NOT NULL
            REFERENCES experiment.v022_result_evidence_snapshot,
          baseline_result_evidence_snapshot_id uuid NULL
            REFERENCES experiment.v022_result_evidence_snapshot,
          result_comparison_id uuid NULL REFERENCES experiment.v022_result_comparison,
          baseline_kind varchar(40) NOT NULL CHECK (
            baseline_kind IN ('defense_none','deterministic_aggregation')
          ),
          assessment_version integer NOT NULL CHECK (assessment_version >= 1),
          status varchar(20) NOT NULL CHECK (status IN ('matched','missing')),
          reason_codes jsonb NOT NULL,
          assessment_fingerprint varchar(64) NOT NULL UNIQUE
            CHECK (assessment_fingerprint ~ '^[0-9a-f]{64}$'),
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (subject_result_evidence_snapshot_id,baseline_kind,assessment_version),
          CHECK (
            (status='matched' AND baseline_result_evidence_snapshot_id IS NOT NULL
              AND result_comparison_id IS NOT NULL AND jsonb_array_length(reason_codes)=0)
            OR
            (status='missing' AND baseline_result_evidence_snapshot_id IS NULL
              AND result_comparison_id IS NULL AND jsonb_array_length(reason_codes)>0)
          ),
          CHECK (baseline_result_evidence_snapshot_id IS NULL OR
                 baseline_result_evidence_snapshot_id <> subject_result_evidence_snapshot_id)
        );
        """
    )
    op.execute(
        """
        CREATE FUNCTION experiment.validate_v022_result_comparison()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE left_status varchar; right_status varchar;
        BEGIN
          PERFORM data.assert_artifact_draft(NEW.artifact_id);
          SELECT artifact.status INTO left_status
            FROM experiment.v022_result_evidence_snapshot evidence
            JOIN lineage.artifact artifact ON artifact.artifact_id=evidence.artifact_id
           WHERE evidence.result_evidence_snapshot_id=NEW.left_result_evidence_snapshot_id;
          SELECT artifact.status INTO right_status
            FROM experiment.v022_result_evidence_snapshot evidence
            JOIN lineage.artifact artifact ON artifact.artifact_id=evidence.artifact_id
           WHERE evidence.result_evidence_snapshot_id=NEW.right_result_evidence_snapshot_id;
          IF left_status <> 'published' OR right_status <> 'published' THEN
            RAISE EXCEPTION 'Comparison requires two published Result Evidence Snapshots';
          END IF;
          IF NEW.classification IN ('replication','controlled','multi_axis') AND
             NEW.left_protected_context_fingerprint <> NEW.right_protected_context_fingerprint THEN
            RAISE EXCEPTION 'Comparable Results must share protected context';
          END IF;
          IF NEW.classification='controlled' AND jsonb_array_length(NEW.changed_dimensions) <> 1 THEN
            RAISE EXCEPTION 'Controlled comparison requires exactly one changed dimension';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_result_comparison_validate
          BEFORE INSERT ON experiment.v022_result_comparison
          FOR EACH ROW EXECUTE FUNCTION experiment.validate_v022_result_comparison();

        CREATE FUNCTION experiment.validate_v022_matched_baseline()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE comparison_row record; expected_dimension varchar;
                subject_configuration jsonb; baseline_configuration jsonb;
        BEGIN
          PERFORM data.assert_artifact_draft(NEW.artifact_id);
          IF NEW.status='matched' THEN
            SELECT * INTO comparison_row FROM experiment.v022_result_comparison
             WHERE result_comparison_id=NEW.result_comparison_id;
            IF comparison_row.classification <> 'controlled' OR
               NOT (
                 (comparison_row.left_result_evidence_snapshot_id=NEW.subject_result_evidence_snapshot_id AND
                  comparison_row.right_result_evidence_snapshot_id=NEW.baseline_result_evidence_snapshot_id) OR
                 (comparison_row.right_result_evidence_snapshot_id=NEW.subject_result_evidence_snapshot_id AND
                  comparison_row.left_result_evidence_snapshot_id=NEW.baseline_result_evidence_snapshot_id)
               ) THEN
              RAISE EXCEPTION 'Matched baseline requires its exact controlled Comparison';
            END IF;
            expected_dimension := CASE NEW.baseline_kind
              WHEN 'defense_none' THEN 'defense_package'
              WHEN 'deterministic_aggregation' THEN 'aggregation_algorithm'
            END;
            IF comparison_row.changed_dimensions <> jsonb_build_array(expected_dimension) THEN
              RAISE EXCEPTION 'Matched baseline Comparison changes the wrong treatment dimension';
            END IF;
            SELECT configuration.semantic_identity_document INTO subject_configuration
              FROM experiment.v022_result_evidence_snapshot evidence
              JOIN experiment.v022_research_configuration_snapshot configuration
                ON configuration.configuration_snapshot_id=evidence.configuration_snapshot_id
             WHERE evidence.result_evidence_snapshot_id=NEW.subject_result_evidence_snapshot_id;
            SELECT configuration.semantic_identity_document INTO baseline_configuration
              FROM experiment.v022_result_evidence_snapshot evidence
              JOIN experiment.v022_research_configuration_snapshot configuration
                ON configuration.configuration_snapshot_id=evidence.configuration_snapshot_id
             WHERE evidence.result_evidence_snapshot_id=NEW.baseline_result_evidence_snapshot_id;
            IF NEW.baseline_kind='defense_none' AND NOT (
              subject_configuration->'defense' IS DISTINCT FROM 'null'::jsonb AND
              baseline_configuration->'defense' = 'null'::jsonb
            ) THEN
              RAISE EXCEPTION 'Defense-none baseline must point from Defense to none';
            END IF;
            IF NEW.baseline_kind='deterministic_aggregation' AND NOT (
              subject_configuration #>> '{aggregation,execution_mode}' <> 'deterministic' AND
              baseline_configuration #>> '{aggregation,execution_mode}' = 'deterministic'
            ) THEN
              RAISE EXCEPTION 'Deterministic Aggregation baseline must point from trainable to deterministic';
            END IF;
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_v022_matched_baseline_validate
          BEFORE INSERT ON experiment.v022_matched_baseline_assessment
          FOR EACH ROW EXECUTE FUNCTION experiment.validate_v022_matched_baseline();
        """
    )
    for table in ("v022_result_comparison", "v022_matched_baseline_assessment"):
        op.execute(
            f"CREATE TRIGGER trg_{table}_append_only BEFORE UPDATE OR DELETE "
            f"ON experiment.{table} FOR EACH ROW EXECUTE FUNCTION lineage.reject_record_mutation()"
        )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS experiment.validate_v022_matched_baseline() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS experiment.validate_v022_result_comparison() CASCADE")
    op.drop_table("v022_matched_baseline_assessment", schema="experiment")
    op.drop_table("v022_result_comparison", schema="experiment")
