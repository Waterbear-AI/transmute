-- Add self_assessed_readiness column to users table.
-- This column tracks whether a user has self-declared readiness for graduation,
-- enabling the deterministic graduation gate.

-- UP migration
ALTER TABLE users ADD COLUMN self_assessed_readiness INTEGER NOT NULL DEFAULT 0;

-- DOWN migration
-- ALTER TABLE users DROP COLUMN self_assessed_readiness;
