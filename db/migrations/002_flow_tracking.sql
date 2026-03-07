-- Add flow_data column to profile_snapshots for storing flow computation results as JSON
ALTER TABLE profile_snapshots ADD COLUMN flow_data TEXT;

-- Moral ledger for tracking Moral Capital (C+) and Moral Debt (C-)
CREATE TABLE moral_ledger (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id),
    snapshot_id TEXT REFERENCES profile_snapshots(id),
    c_plus      REAL NOT NULL DEFAULT 0.0,
    c_minus     REAL NOT NULL DEFAULT 0.0,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_moral_ledger_user_id ON moral_ledger(user_id);
CREATE INDEX idx_moral_ledger_snapshot_id ON moral_ledger(snapshot_id);
