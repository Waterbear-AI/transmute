-- Add nullable title column to adk_sessions for user-chosen session labels.

-- UP migration
ALTER TABLE adk_sessions ADD COLUMN title TEXT;

-- DOWN migration
-- ALTER TABLE adk_sessions DROP COLUMN title;
