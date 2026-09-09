-- Manual recurring finance.
-- Recurring expenses are templates only; actual expenses are created by an explicit "Провести" action.
-- Recurring incomes are templates; actual receipts are created by an explicit "Получено" action.
-- Safe to run once against the current schema.

CREATE TABLE IF NOT EXISTS recurring_incomes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    amount NUMERIC(10, 2) NOT NULL CHECK (amount > 0),
    payment_method expense_payment_method NOT NULL DEFAULT 'transfer',
    day_of_month INTEGER NOT NULL DEFAULT 1 CHECK (day_of_month BETWEEN 1 AND 31),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    description TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_recurring_incomes_active
    ON recurring_incomes(is_active);

CREATE TABLE IF NOT EXISTS recurring_income_records (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    recurring_income_id UUID NOT NULL,
    recurring_period DATE NOT NULL,
    amount NUMERIC(10, 2) NOT NULL CHECK (amount > 0),
    payment_method expense_payment_method NOT NULL DEFAULT 'transfer',
    received_date DATE NOT NULL DEFAULT CURRENT_DATE,
    comment TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_cancelled BOOLEAN NOT NULL DEFAULT FALSE,
    cancelled_at TIMESTAMPTZ NULL,
    cancel_reason TEXT NULL,
    CONSTRAINT fk_recurring_income_records_income
        FOREIGN KEY (recurring_income_id)
        REFERENCES recurring_incomes(id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_recurring_income_records_income_id
    ON recurring_income_records(recurring_income_id);

CREATE INDEX IF NOT EXISTS ix_recurring_income_records_period
    ON recurring_income_records(recurring_period);

CREATE INDEX IF NOT EXISTS ix_recurring_income_records_active
    ON recurring_income_records(is_cancelled);

CREATE UNIQUE INDEX IF NOT EXISTS uq_recurring_income_records_active_period
    ON recurring_income_records(recurring_income_id, recurring_period)
    WHERE is_cancelled = FALSE;

CREATE OR REPLACE FUNCTION set_recurring_income_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_recurring_incomes_updated_at ON recurring_incomes;
CREATE TRIGGER trg_recurring_incomes_updated_at
BEFORE UPDATE ON recurring_incomes
FOR EACH ROW EXECUTE FUNCTION set_recurring_income_updated_at();
