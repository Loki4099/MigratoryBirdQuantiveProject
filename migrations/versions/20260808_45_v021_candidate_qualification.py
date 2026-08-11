"""Allow warning-bearing Research Candidates without claiming Formal eligibility.

Revision ID: 20260808_45_v021_candidate
Revises: 20260806_44_v021_materialization
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260808_45_v021_candidate"
down_revision: str | None = "20260806_44_v021_materialization"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # `product_eligible` now means eligible for a warning-bearing Research Candidate.
    # Formal/deployable eligibility remains separately represented by `formal_eligible`.
    op.drop_constraint(
        "ck_qualification_bundle_product_requires_formal",
        "qualification_bundle",
        schema="experiment",
        type_="check",
    )


def downgrade() -> None:
    op.create_check_constraint(
        "ck_qualification_bundle_product_requires_formal",
        "qualification_bundle",
        "product_eligible = FALSE OR formal_eligible = TRUE",
        schema="experiment",
    )
