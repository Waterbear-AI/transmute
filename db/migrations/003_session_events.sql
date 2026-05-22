-- Add events_json column to persist conversation history
ALTER TABLE adk_sessions ADD COLUMN events_json JSON DEFAULT '[]';
