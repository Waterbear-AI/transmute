-- LLM call history table for auditing individual LLM calls made on behalf of users.
-- Stores session, user, agent author, phase, model, token counts, and cost.

-- UP migration
CREATE TABLE llm_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT REFERENCES adk_sessions(session_id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    author TEXT,
    phase TEXT,
    model_id TEXT,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0.0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_llm_calls_user_id ON llm_calls(user_id);
CREATE INDEX idx_llm_calls_user_created ON llm_calls(user_id, id DESC);

-- DOWN migration
-- DROP TABLE llm_calls;
