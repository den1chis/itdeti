-- Finance hardening: editable/cancellable operations and recurring expenses.
-- This migration is intentionally idempotent where PostgreSQL permits it.

ALTER TYPE expense_category ADD VALUE IF NOT EXISTS 'rent';
ALTER TYPE expense_category ADD VALUE IF NOT EXISTS 'tax';
ALTER TYPE expense_category ADD VALUE IF NOT EXISTS 'advertising';
ALTER TYPE expense_category ADD VALUE IF NOT EXISTS 'materials';
ALTER TYPE expense_category ADD VALUE IF NOT EXISTS 'bank_fee';
ALTER TYPE expense_category ADD VALUE IF NOT EXISTS 'transport';

ALTER TABLE payments
    ADD COLUMN IF NOT EXISTS is_cancelled BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS cancelled_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS cancel_reason TEXT NULL;

ALTER TABLE expenses
    ADD COLUMN IF NOT EXISTS recurring_expense_id UUID NULL,
    ADD COLUMN IF NOT EXISTS recurring_period DATE NULL,
    ADD COLUMN IF NOT EXISTS is_cancelled BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS cancelled_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS cancel_reason TEXT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'fk_expenses_recurring_expense'
    ) THEN
        ALTER TABLE expenses
            ADD CONSTRAINT fk_expenses_recurring_expense
            FOREIGN KEY (recurring_expense_id)
            REFERENCES recurring_expenses(id)
            ON DELETE SET NULL;
    END IF;
EXCEPTION
    WHEN undefined_table THEN
        NULL;
END $$;

CREATE INDEX IF NOT EXISTS ix_payments_active
    ON payments(is_cancelled);

CREATE INDEX IF NOT EXISTS ix_expenses_active
    ON expenses(is_cancelled);

CREATE INDEX IF NOT EXISTS ix_expenses_recurring_expense_id
    ON expenses(recurring_expense_id);

CREATE UNIQUE INDEX IF NOT EXISTS uq_expenses_recurring_period
    ON expenses(recurring_expense_id, recurring_period)
    WHERE recurring_expense_id IS NOT NULL AND recurring_period IS NOT NULL;

CREATE TABLE IF NOT EXISTS recurring_expenses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    category expense_category NOT NULL,
    amount NUMERIC(10, 2) NOT NULL CHECK (amount > 0),
    payment_method expense_payment_method NOT NULL DEFAULT 'cash',
    day_of_month INTEGER NOT NULL DEFAULT 1 CHECK (day_of_month BETWEEN 1 AND 31),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    description TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_recurring_expenses_active
    ON recurring_expenses(is_active);

CREATE OR REPLACE FUNCTION set_recurring_expense_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_recurring_expenses_updated_at ON recurring_expenses;
CREATE TRIGGER trg_recurring_expenses_updated_at
BEFORE UPDATE ON recurring_expenses
FOR EACH ROW EXECUTE FUNCTION set_recurring_expense_updated_at();
