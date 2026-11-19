-- 016_savings.sql — night 6: «Накопления» on the Финансы page (deposits and cash, only tenge).
-- Additive only: two new tables with RLS and their indexes. Nothing is dropped, renamed, retyped or deleted,
-- no existing finance table is touched. Safe to run more than once.
-- Deploy order: this migration first, then the code (the old code does not notice the new tables).
--   dev:  python scripts/apply_migration.py 016
--   prod: python scripts/prod_ops.py --prod migrate 016_savings.sql   (after backup)
--
-- savings_accounts   a deposit (name) or cash. Never deleted while it has movements: archived instead.
-- savings_movements  deposit / withdrawal in WHOLE tenge (bigint > 0). Balance of an account = sum of its
--                    movements that are not cancelled (deposit +, withdrawal -). The opening balance is the first
--                    movement. A movement is cancelled (is_cancelled), never deleted, like the other finance rows.
-- RLS: enabled without policies, like the other finance tables: only the server (table owner) reads and writes.

BEGIN;

CREATE TABLE IF NOT EXISTS public.savings_accounts (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name         TEXT        NOT NULL CHECK (char_length(btrim(name)) BETWEEN 1 AND 100),
    kind         TEXT        NOT NULL CHECK (kind IN ('deposit', 'cash')),
    is_archived  BOOLEAN     NOT NULL DEFAULT false,
    archived_at  TIMESTAMPTZ NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE public.savings_accounts ENABLE ROW LEVEL SECURITY;

CREATE TABLE IF NOT EXISTS public.savings_movements (
    id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id     UUID        NOT NULL REFERENCES public.savings_accounts(id) ON DELETE RESTRICT,
    kind           TEXT        NOT NULL CHECK (kind IN ('deposit', 'withdrawal')),
    amount         BIGINT      NOT NULL CHECK (amount > 0 AND amount <= 1000000000000),
    movement_date  DATE        NOT NULL,
    comment        TEXT        NULL CHECK (comment IS NULL OR char_length(comment) <= 500),
    created_by     UUID        NULL REFERENCES public.users(id) ON DELETE SET NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_cancelled   BOOLEAN     NOT NULL DEFAULT false,
    cancelled_at   TIMESTAMPTZ NULL
);
ALTER TABLE public.savings_movements ENABLE ROW LEVEL SECURITY;
CREATE INDEX IF NOT EXISTS idx_savings_movements_account_id ON public.savings_movements(account_id);
CREATE INDEX IF NOT EXISTS idx_savings_movements_movement_date ON public.savings_movements(movement_date);

COMMIT;
