-- Add education_content table for the education "learning journal".
-- Stores the exact teaching content delivered to a user for each
-- dimension/category pair, captured via present_education_content so the
-- Education tab can render a persistent, read-only record of what the user
-- has been taught. Composite PK enables an atomic upsert when a category is
-- re-taught (content is overwritten, not duplicated).

-- UP migration
CREATE TABLE IF NOT EXISTS education_content (
    user_id     TEXT NOT NULL REFERENCES users(id),
    dimension   TEXT NOT NULL,
    category    TEXT NOT NULL,
    content     TEXT NOT NULL,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, dimension, category)
);

-- DOWN migration
-- DROP TABLE education_content;
