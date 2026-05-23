ALTER TABLE users ADD COLUMN reassessment_cycle INTEGER NOT NULL DEFAULT 0;

CREATE TABLE dimension_assessment_state (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    dimension TEXT NOT NULL,
    last_assessed_cycle INTEGER NOT NULL DEFAULT 0,
    last_assessment_kind TEXT,
    last_score REAL,
    flagged_for_full_reassessment BOOLEAN NOT NULL DEFAULT 0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, dimension)
);

CREATE INDEX idx_das_user_id ON dimension_assessment_state(user_id);
