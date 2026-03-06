-- Version tracking for migrations
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Users
CREATE TABLE users (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    email           TEXT UNIQUE NOT NULL,
    password_hash   TEXT NOT NULL,
    current_phase   TEXT DEFAULT 'orientation',
    graduated_at    TIMESTAMP,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Assessment state
CREATE TABLE assessment_state (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL REFERENCES users(id),
    assessment_type TEXT NOT NULL,
    responses       JSON,
    scenario_responses JSON,
    completed_at    TIMESTAMP,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Profile snapshots
CREATE TABLE profile_snapshots (
    id                  TEXT PRIMARY KEY,
    user_id             TEXT NOT NULL REFERENCES users(id),
    assessment_id       TEXT REFERENCES assessment_state(id),
    snapshot            JSON,
    interpretation      JSON,
    spider_chart        BLOB,
    previous_snapshot_id TEXT REFERENCES profile_snapshots(id),
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Education progress
CREATE TABLE education_progress (
    user_id     TEXT NOT NULL REFERENCES users(id),
    progress    JSON,
    PRIMARY KEY (user_id)
);

-- Development roadmap
CREATE TABLE development_roadmap (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL REFERENCES users(id),
    parent_roadmap_id TEXT REFERENCES development_roadmap(id),
    roadmap         JSON,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Practice journal
CREATE TABLE practice_journal (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id),
    practice_id TEXT,
    reflection  TEXT,
    self_rating INTEGER,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Graduation record
CREATE TABLE graduation_record (
    id                    TEXT PRIMARY KEY,
    user_id               TEXT NOT NULL REFERENCES users(id),
    final_snapshot_id     TEXT REFERENCES profile_snapshots(id),
    initial_snapshot_id   TEXT REFERENCES profile_snapshots(id),
    practice_map          JSON,
    pattern_narrative     TEXT,
    graduation_indicators JSON,
    created_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Check-in log
CREATE TABLE check_in_log (
    id                      TEXT PRIMARY KEY,
    user_id                 TEXT NOT NULL REFERENCES users(id),
    snapshot_id             TEXT REFERENCES profile_snapshots(id),
    graduation_snapshot_id  TEXT REFERENCES profile_snapshots(id),
    regression_detected     BOOLEAN,
    re_entered_development  BOOLEAN DEFAULT FALSE,
    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Safety log
CREATE TABLE safety_log (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id),
    reason      TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ADK sessions
CREATE TABLE adk_sessions (
    user_id             TEXT NOT NULL,
    session_id          TEXT NOT NULL,
    app_name            TEXT,
    session_state       TEXT,
    archived            BOOLEAN DEFAULT FALSE,
    total_input_tokens  INTEGER DEFAULT 0,
    total_output_tokens INTEGER DEFAULT 0,
    estimated_cost_usd  REAL DEFAULT 0.0,
    updated_at          TIMESTAMP,
    PRIMARY KEY (user_id, session_id)
);
