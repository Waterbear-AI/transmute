-- Add tiered-assessment tracking columns to assessment_state.
-- Supports the transmute-first, adaptive, validated-scale assessment redesign:
-- assessment_tier tracks progression through the tiered flow, flagged_dimensions
-- and deep_dive_dimensions record which dimensions triggered follow-up, and
-- early_result stores the early transmute-tier result payload.

-- UP migration
ALTER TABLE assessment_state ADD COLUMN assessment_tier TEXT NOT NULL DEFAULT 'transmute_core';
ALTER TABLE assessment_state ADD COLUMN flagged_dimensions TEXT; -- JSON array
ALTER TABLE assessment_state ADD COLUMN deep_dive_dimensions TEXT; -- JSON array
ALTER TABLE assessment_state ADD COLUMN early_result TEXT; -- JSON object

-- DOWN migration
-- ALTER TABLE assessment_state DROP COLUMN early_result;
-- ALTER TABLE assessment_state DROP COLUMN deep_dive_dimensions;
-- ALTER TABLE assessment_state DROP COLUMN flagged_dimensions;
-- ALTER TABLE assessment_state DROP COLUMN assessment_tier;
