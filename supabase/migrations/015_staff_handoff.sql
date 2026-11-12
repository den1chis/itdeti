-- 015_staff_handoff.sql — night 5: one-time codes for "CRM -> learning platform" without typing the password.
-- Additive only: one new table with RLS and its indexes. Nothing is dropped, renamed, retyped or deleted.
-- Safe to run more than once.
-- Deploy order: this migration first, then the code (the old code does not notice the new table).
--   dev:  python scripts/apply_migration.py 015
--   prod: python scripts/prod_ops.py --prod migrate 015_staff_handoff.sql   (after backup)
--
-- A code is issued by POST /auth/handoff (a staff member with a live CRM session) and redeemed once by
-- POST /auth/handoff/redeem on the learning platform within 60 seconds. Only the SHA-256 of the code is
-- stored; the code itself exists only in the CRM tab and in the URL fragment of the platform.
--   target      what the platform opens: 'profile' | 'catalog' | 'student:<uuid>'
--   expires_at  issued_at + 60 s; expired rows are deleted when new codes are issued

BEGIN;

CREATE TABLE IF NOT EXISTS public.auth_handoff_codes (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    code_hash   TEXT        NOT NULL,
    user_id     UUID        NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    target      TEXT        NOT NULL,
    expires_at  TIMESTAMPTZ NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_ip  TEXT        NULL
);
ALTER TABLE public.auth_handoff_codes ENABLE ROW LEVEL SECURITY;
CREATE UNIQUE INDEX IF NOT EXISTS uq_auth_handoff_codes_code_hash ON public.auth_handoff_codes(code_hash);
CREATE INDEX IF NOT EXISTS idx_auth_handoff_codes_expires_at ON public.auth_handoff_codes(expires_at);
CREATE INDEX IF NOT EXISTS idx_auth_handoff_codes_user_id ON public.auth_handoff_codes(user_id);

COMMIT;
