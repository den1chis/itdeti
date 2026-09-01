-- Standalone masterclasses are stored as regular events.
-- The existing event_type enum must accept the new value in production.
ALTER TYPE event_type ADD VALUE IF NOT EXISTS 'masterclass';
