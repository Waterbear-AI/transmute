ALTER TABLE practice_journal ADD COLUMN dimension TEXT;
ALTER TABLE practice_journal ADD COLUMN sub_dimension TEXT;
ALTER TABLE practice_journal ADD COLUMN transmutation_operation TEXT;

CREATE TABLE roadmap_practices (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    roadmap_id TEXT REFERENCES development_roadmap(id),
    practice_id TEXT NOT NULL,
    title TEXT,
    dimension TEXT NOT NULL,
    sub_dimension TEXT,
    transmutation_operation TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, practice_id)
);

CREATE INDEX idx_roadmap_practices_user_id ON roadmap_practices(user_id);
