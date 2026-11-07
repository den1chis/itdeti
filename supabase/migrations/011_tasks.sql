-- Tasks / to-do list: projects, tasks (one-off and recurring), per-occurrence overrides.
-- Purely additive: creates NEW tables only. Touches no existing table, type, function or data.
-- Safe to run more than once (IF NOT EXISTS everywhere).

BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS task_projects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    color TEXT NOT NULL DEFAULT '#4f46e5',
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tasks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title TEXT NOT NULL,
    notes TEXT NULL,
    project_id UUID NULL REFERENCES task_projects(id) ON DELETE SET NULL,
    due_date DATE NULL,
    is_done BOOLEAN NOT NULL DEFAULT FALSE,
    done_at TIMESTAMPTZ NULL,
    repeat TEXT NOT NULL DEFAULT 'none' CHECK (repeat IN ('none', 'daily', 'weekly', 'monthly', 'yearly')),
    repeat_interval INTEGER NOT NULL DEFAULT 1 CHECK (repeat_interval BETWEEN 1 AND 365),
    repeat_weekdays SMALLINT[] NOT NULL DEFAULT '{}',
    repeat_until DATE NULL,
    source TEXT NOT NULL DEFAULT 'manual',
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_tasks_repeat_needs_date CHECK (repeat = 'none' OR due_date IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS ix_tasks_due_date ON tasks(due_date);
CREATE INDEX IF NOT EXISTS ix_tasks_project_id ON tasks(project_id);

CREATE TABLE IF NOT EXISTS task_occurrences (
    task_id UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    occurrence_date DATE NOT NULL,
    done_at TIMESTAMPTZ NULL,
    moved_to DATE NULL,
    is_skipped BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (task_id, occurrence_date)
);

CREATE INDEX IF NOT EXISTS ix_task_occurrences_moved_to ON task_occurrences(moved_to);

CREATE OR REPLACE FUNCTION tasks_set_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_task_projects_updated_at ON task_projects;
CREATE TRIGGER trg_task_projects_updated_at BEFORE UPDATE ON task_projects
FOR EACH ROW EXECUTE FUNCTION tasks_set_updated_at();

DROP TRIGGER IF EXISTS trg_tasks_updated_at ON tasks;
CREATE TRIGGER trg_tasks_updated_at BEFORE UPDATE ON tasks
FOR EACH ROW EXECUTE FUNCTION tasks_set_updated_at();

DROP TRIGGER IF EXISTS trg_task_occurrences_updated_at ON task_occurrences;
CREATE TRIGGER trg_task_occurrences_updated_at BEFORE UPDATE ON task_occurrences
FOR EACH ROW EXECUTE FUNCTION tasks_set_updated_at();

-- Starter projects (only if there are none yet).
INSERT INTO task_projects (name, color, sort_order)
SELECT v.name, v.color, v.sort_order
FROM (VALUES ('ITdeti', '#4f46e5', 1), ('Учёба', '#0891b2', 2), ('Личное', '#d97706', 3)) AS v(name, color, sort_order)
WHERE NOT EXISTS (SELECT 1 FROM task_projects);

COMMIT;
