# ruff: noqa: E501
"""Version split-normalized total-return market reconstruction.

Revision ID: 20260820_134_v022_recon_v2
Revises: 20260819_133_v022_round_gc
"""

from __future__ import annotations

from alembic import op

revision = "20260820_134_v022_recon_v2"
down_revision = "20260819_133_v022_round_gc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$ DECLARE constraint_name name;
        BEGIN
          SELECT constraint_item.conname INTO constraint_name
            FROM pg_constraint constraint_item
           WHERE constraint_item.conrelid=
                   'data.v022_market_reconciliation_plan'::regclass
             AND constraint_item.contype='c'
             AND pg_get_constraintdef(constraint_item.oid) LIKE
                   '%reconstruction_policy%raw_ohlcv_actions_backward_total_return_v1%';
          IF constraint_name IS NULL THEN
            RAISE EXCEPTION 'Legacy reconciliation-plan policy constraint not found';
          END IF;
          EXECUTE format(
            'ALTER TABLE data.v022_market_reconciliation_plan DROP CONSTRAINT %I',
            constraint_name
          );
        END $$;
        ALTER TABLE data.v022_market_reconciliation_plan
          ADD CONSTRAINT ck_v022_recon_plan_policy_v2 CHECK (
            reconstruction_policy IN (
              'raw_ohlcv_actions_backward_total_return_v1',
              'split_normalized_ohlcv_dividends_backward_total_return_v2'
            )
          );

        DO $$ DECLARE constraint_name name;
        BEGIN
          SELECT constraint_item.conname INTO constraint_name
            FROM pg_constraint constraint_item
           WHERE constraint_item.conrelid=
                   'data.v022_reconciled_market_dataset_binding'::regclass
             AND constraint_item.contype='c'
             AND pg_get_constraintdef(constraint_item.oid) LIKE
                   '%reconstruction_policy%raw_ohlcv_actions_backward_total_return_v1%';
          IF constraint_name IS NULL THEN
            RAISE EXCEPTION 'Legacy reconciliation-binding policy constraint not found';
          END IF;
          EXECUTE format(
            'ALTER TABLE data.v022_reconciled_market_dataset_binding DROP CONSTRAINT %I',
            constraint_name
          );
        END $$;
        ALTER TABLE data.v022_reconciled_market_dataset_binding
          ADD CONSTRAINT ck_v022_recon_binding_policy_v2 CHECK (
            reconstruction_policy IN (
              'raw_ohlcv_actions_backward_total_return_v1',
              'split_normalized_ohlcv_dividends_backward_total_return_v2'
            )
          );

        DO $$ DECLARE constraint_name name;
        BEGIN
          SELECT constraint_item.conname INTO constraint_name
            FROM pg_constraint constraint_item
           WHERE constraint_item.conrelid=
                   'data.v022_reconciled_market_dataset_binding'::regclass
             AND constraint_item.contype='c'
             AND pg_get_constraintdef(constraint_item.oid) LIKE
                   '%price_semantics%historical_constituent_pit__frozen_reconciled_retrospective_prices%';
          IF constraint_name IS NULL THEN
            RAISE EXCEPTION 'Legacy reconciliation price-semantics constraint not found';
          END IF;
          EXECUTE format(
            'ALTER TABLE data.v022_reconciled_market_dataset_binding DROP CONSTRAINT %I',
            constraint_name
          );
        END $$;
        ALTER TABLE data.v022_reconciled_market_dataset_binding
          ADD CONSTRAINT ck_v022_recon_binding_price_semantics_v2 CHECK (
            price_semantics IN (
              'historical_constituent_pit__frozen_reconciled_retrospective_prices',
              'historical_constituent_pit__frozen_reconciled_retrospective_split_normalized_total_return_prices'
            )
          );

        COMMENT ON COLUMN data.v022_market_reconciliation_plan.reconstruction_policy IS
          'Immutable reconstruction contract; v1 is retained for replay, v2 is the split-normalized input contract.';
        COMMENT ON COLUMN data.v022_reconciled_market_dataset_binding.price_semantics IS
          'Exact price-input and total-return semantics inherited from the immutable reconciliation policy.';
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM data.v022_market_reconciliation_plan
             WHERE reconstruction_policy=
               'split_normalized_ohlcv_dividends_backward_total_return_v2'
          ) OR EXISTS (
            SELECT 1 FROM data.v022_reconciled_market_dataset_binding
             WHERE reconstruction_policy=
               'split_normalized_ohlcv_dividends_backward_total_return_v2'
                OR price_semantics=
               'historical_constituent_pit__frozen_reconciled_retrospective_split_normalized_total_return_prices'
          ) THEN
            RAISE EXCEPTION
              'Cannot downgrade with v0.22 split-normalized reconstruction v2 evidence';
          END IF;
        END $$;

        ALTER TABLE data.v022_market_reconciliation_plan
          DROP CONSTRAINT ck_v022_recon_plan_policy_v2;
        ALTER TABLE data.v022_market_reconciliation_plan
          ADD CONSTRAINT ck_v022_recon_plan_policy_v1
          CHECK (reconstruction_policy='raw_ohlcv_actions_backward_total_return_v1');

        ALTER TABLE data.v022_reconciled_market_dataset_binding
          DROP CONSTRAINT ck_v022_recon_binding_policy_v2;
        ALTER TABLE data.v022_reconciled_market_dataset_binding
          ADD CONSTRAINT ck_v022_recon_binding_policy_v1
          CHECK (reconstruction_policy='raw_ohlcv_actions_backward_total_return_v1');

        ALTER TABLE data.v022_reconciled_market_dataset_binding
          DROP CONSTRAINT ck_v022_recon_binding_price_semantics_v2;
        ALTER TABLE data.v022_reconciled_market_dataset_binding
          ADD CONSTRAINT ck_v022_recon_binding_price_semantics_v1
          CHECK (
            price_semantics=
              'historical_constituent_pit__frozen_reconciled_retrospective_prices'
          );

        COMMENT ON COLUMN data.v022_market_reconciliation_plan.reconstruction_policy IS NULL;
        COMMENT ON COLUMN data.v022_reconciled_market_dataset_binding.price_semantics IS NULL;
        """
    )
