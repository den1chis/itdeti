-- 014_security.sql — night 4: CRM login throttling, audit log, hashed refresh tokens.
-- Additive only: new tables, new column, indexes, filling of the NEW column. Nothing is dropped, renamed,
-- retyped or deleted; existing columns keep their values. Safe to run more than once.
-- Deploy order: this migration first, then the code (the old code does not notice the new objects).
--   dev:  python scripts/apply_migration.py 014
--   prod: python scripts/prod_ops.py --prod migrate 014_security.sql   (after backup, see docs/RUNBOOK_EMERGENCY.md)

BEGIN;

-- 1. CRM login throttling, one row per (email, client IP). Emails are stored lower(trim()).
--    failed_count      wrong passwords in a row for the pair (reset by a successful login or after 15 quiet minutes)
--    locked_until      the pair is refused until this moment
--    window_*          wrong passwords of the pair in the current 15-minute window (summed per email for the
--                      distributed-guessing lock)
--    last_success_at   a successful login from this IP: for 30 days the IP is "trusted" for this email
CREATE TABLE IF NOT EXISTS public.auth_login_state (
    email            TEXT        NOT NULL,
    ip               TEXT        NOT NULL,
    failed_count     INTEGER     NOT NULL DEFAULT 0,
    locked_until     TIMESTAMPTZ NULL,
    last_failed_at   TIMESTAMPTZ NULL,
    window_started_at TIMESTAMPTZ NULL,
    window_failed    INTEGER     NOT NULL DEFAULT 0,
    last_success_at  TIMESTAMPTZ NULL,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (email, ip)
);
ALTER TABLE public.auth_login_state ENABLE ROW LEVEL SECURITY;
CREATE INDEX IF NOT EXISTS idx_auth_login_state_updated_at ON public.auth_login_state(updated_at);

-- 2. Lock of one email for every not-trusted IP (30 wrong passwords in 15 minutes from any addresses).
CREATE TABLE IF NOT EXISTS public.auth_email_locks (
    email        TEXT        PRIMARY KEY,
    locked_until TIMESTAMPTZ NOT NULL,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE public.auth_email_locks ENABLE ROW LEVEL SECURITY;

-- 3. Audit log: only the most sensitive actions. details never holds passwords, tokens or keys.
CREATE TABLE IF NOT EXISTS public.audit_log (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor_user_id UUID        NULL,
    actor_kind    TEXT        NOT NULL DEFAULT 'user',
    action        TEXT        NOT NULL,
    target_type   TEXT        NULL,
    target_id     TEXT        NULL,
    ip            TEXT        NULL,
    details       JSONB       NOT NULL DEFAULT '{}'::jsonb
);
ALTER TABLE public.audit_log ENABLE ROW LEVEL SECURITY;
CREATE INDEX IF NOT EXISTS idx_audit_log_created_at ON public.audit_log(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_log_action_created_at ON public.audit_log(action, created_at DESC);

-- 4. Refresh tokens: SHA-256 of the token instead of the token itself. The old column "token" stays
--    (NOT NULL, unique): new rows get 'sha256:<hash>' there, never the token. Existing rows keep their
--    value in "token" and get the hash in the NEW column, so they are found by hash at once; they disappear
--    as they are used (rotation) or expire (30 days).
ALTER TABLE public.refresh_tokens ADD COLUMN IF NOT EXISTS token_hash TEXT NULL;
UPDATE public.refresh_tokens
   SET token_hash = encode(sha256(convert_to(token, 'UTF8')), 'hex')
 WHERE token_hash IS NULL
   AND token NOT LIKE 'sha256:%';
CREATE UNIQUE INDEX IF NOT EXISTS uq_refresh_tokens_token_hash ON public.refresh_tokens(token_hash);
CREATE INDEX IF NOT EXISTS idx_refresh_tokens_expires_at ON public.refresh_tokens(expires_at);

COMMIT;
