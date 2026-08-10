"""Allow one Strategy Product and Model Dataset to be republished by a new engine.

Revision ID: 20260805_28_v02_target_engine
Revises: 20260805_27_v02_cohort_ctx
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260805_28_v02_target_engine"
down_revision: str | None = "20260805_27_v02_cohort_ctx"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Engine identity lives on the parent Portfolio Target Path.  The former unique key
    # omitted it and therefore made legitimate immutable engine upgrades impossible.
    op.drop_constraint(
        "uq_model_strategy_target_exact_inputs",
        "model_strategy_target_path",
        schema="strategy",
        type_="unique",
    )
    op.create_index(
        "ix_model_strategy_target_product_dataset",
        "model_strategy_target_path",
        ["strategy_product_version_id", "model_dataset_id"],
        schema="strategy",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_model_strategy_target_product_dataset",
        table_name="model_strategy_target_path",
        schema="strategy",
    )
    op.create_unique_constraint(
        "uq_model_strategy_target_exact_inputs",
        "model_strategy_target_path",
        ["strategy_product_version_id", "model_dataset_id"],
        schema="strategy",
    )
