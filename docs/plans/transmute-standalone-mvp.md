# Transmutation Engine — Standalone MVP Plan

## Goal

Build a standalone web application with an ADK agent that implements the Transmutation Engine assessment and coaching flow. Includes a web-based chat UI (agent/user chat window with results panel beside it), basic local user management, and SQLite persistence. Designed to run on a local network so multiple people can connect and use it.

Designed for portability: the agent code (`agents/`) can be lifted directly into a WCRP-shell later when building the full SaaS platform. The local user management gets replaced by WCRP's full auth system.

## Distribution Model

- **Local**: Clone repo, supply your LLM API keys, run `docker compose up` — opens web UI on `localhost:54718`
- **OSS**: Free forever. MIT or similar license.
- **Later (SaaS)**: Copy WCRP shell (user management, multi-tenancy, frontend, SSE streaming), drop this agent in. Paid service, ideally philanthropist-funded to be free for users.

---

## Architecture Overview

Follow WCRP's proven ADK patterns exactly, minus the platform infrastructure:

| WCRP Pattern | Standalone Equivalent |
| --- | --- |
| FastAPI + BFF + Next.js frontend | FastAPI backend + lightweight frontend (chat + results panel) |
| PostgreSQL + tenant schemas | SQLite single-file DB (no tenants, single schema) |
| `PostgresSessionService` (custom ADK session backend) | `SqliteSessionService` (same interface, SQLite backend) |
| `LiteLlm` model adapter (Bedrock) | `LiteLlm` model adapter (user's choice: Anthropic, OpenAI, Bedrock, local) |
| Closure-injected tools with `get_tenant_session()` | Closure-injected tools with `get_db_session()` |
| SSE streaming to frontend | SSE streaming to frontend (same pattern) |
| `public.llm_models` table for model selection | `.env` or `config.yaml` for model config |
| JWT + OAuth + MFA + invitations | Simple local auth (create user with name/email/password, session cookie) |

### What Ports Directly to WCRP-Shell Later

- `agents/transmutation/` — entire agent directory (sub-agents, tools, prompts)
- `agents/transmutation/session_service.py` — swap SQLite impl for Postgres impl (same `BaseSessionService` interface)
- Tool closures — change `get_db_session()` to `get_tenant_session(tenant_schema)`
- Prompts — zero changes needed

### What Gets Replaced When Porting

- Lightweight frontend → WCRP's full Next.js app with BFF layer
- SQLite → PostgreSQL with tenant isolation
- `.env` model config → `public.llm_models` table + quota tracking
- Local auth (simple user/password/cookie) → WCRP's JWT + OAuth + MFA + invitations
- Single-schema DB → per-tenant schemas

---

## Agent Architecture

### Root Agent + Sub-Agents (mirrors WCRP's orchestrator pattern)

```text
transmutation_root_agent (orchestrator + orientation)
├── assessment_agent        — Phase 2: Awareness + Transmutation profiling
├── profile_agent           — Phase 3: Interpret and explain the profile
├── education_agent         — Phase 4: Teach transmutarian concepts through their data
├── development_agent       — Phase 5: Prescribe and track practices
├── reassessment_agent      — Phase 6 (loop): Targeted re-assessment + graduation evaluation
├── graduation_agent        — Phase 7: Closing sequence + artifact generation
└── check_in_agent          — Phase 8: Post-graduation periodic assessment
```

Root agent handles Phase 1 (orientation) directly, then calls `get_user_profile()` (like WCRP calls `get_conflict_summary()`), reads `current_phase`, and routes to the appropriate sub-agent based on its `description` field.

**Phase gate design**: While the root agent routes via LLM-interpreted `description` fields, each sub-agent's tools enforce hard phase validation. If `save_assessment_response()` is called but `current_phase != 'assessment'`, the tool rejects the call. This gives LLM flexibility for routing while maintaining deterministic guardrails on state transitions.

**Tool design principle**: Tools fetch their own data internally. The LLM provides only what it uniquely knows — user intent, routing decisions, and values extracted from conversation — never data blobs that already exist in the database. If a tool *can* look something up by ID or closure context, it *must* look it up. This prevents token waste, data corruption, and unnecessary complexity.

**Session segmentation**: Each user login/sitting starts a new ADK session. The agent bootstraps context by calling `get_assessment_state()` or `get_user_profile()` rather than replaying full conversation history. Domain state lives in domain tables, not session state. This prevents context window blowup during the assessment (multiple sessions across 40-55 minutes total). Prior sessions are archived (`archived = true`) when a new session starts.

### Phase Model

```text
orientation -> assessment -> profile -> education -> development -> reassessment
                                                         ^              |
                                                         +--------------+  (active loop)
                                                                        |
                                                                   graduation  (closing sequence)
                                                                        |
                                                                   graduated   (terminal state)
                                                                        |
                                                                    check_in   (periodic return)
                                                                        |
                                                                   graduated   (default return)
```

Valid `current_phase` values: `orientation | assessment | profile | education | development | reassessment | graduation | graduated | check_in`

### Sub-Agent Details

#### 0. Orientation (Root Agent)

**Purpose**: Introduce the user to transmutarianism and set expectations before the assessment begins.

**Delivery**: Hybrid — static content in the Results Panel + agent-driven readiness check in chat.

**Results Panel content** (rendered when `current_phase == 'orientation'`):
- **What is transmutarianism?** — Everyone processes deprivation and fulfillment. Most pass it through unchanged — the Conduit pattern, morally neutral. Transmutarianism maps *how* you process what flows through you across two axes: filter/amplify deprivation, absorb/emit fulfillment. Five patterns: Transmuter, Magnifier, Absorber, Extractor, Conduit. These are operating patterns, not identities.
- **What you'll do here** — Assessment (~200 awareness questions + ~20 behavioral scenarios), profile generation (spider chart + quadrant placement), personalized education, development roadmap with practices. Designed so you need it less over time.
- **What to expect** — Time: 45-55 minutes across multiple sessions, stop and resume anytime. Data stored locally in SQLite. No data leaves your network except LLM API calls. No telemetry.

**Agent exchange** (2-3 turns, ~2-5 minutes):
- Confirms the user has read the orientation
- Asks a grounding question: "What brought you here? What are you hoping to learn about yourself?" (motivation anchor for the assessment)
- Does NOT teach transmutarianism in depth — that's the education phase's job
- Calls `advance_phase('assessment', 'orientation complete')` when ready

**Tools**: `advance_phase(new_phase, reason)` — predicate: user has sent at least one message.

#### 1. Assessment Agent

**Purpose**: Guide the user through the awareness survey + transmutation scenario assessment via a hybrid chat-orchestrated, UI-rendered flow.

**Tools**:

- `get_assessment_state()` — current progress (which questions answered, which remaining, per-dimension completion %, batch-level summaries)
- `present_question_batch(question_ids)` — emits `assessment.question_batch` SSE event; frontend renders clickable Likert cards inline in the chat stream. Questions delivered 2-5 at a time. Likert responses bypass the agent — user clicks go directly to `POST /api/assessment/responses`.
- `present_scenario(scenario_id)` — emits `assessment.scenario` SSE event; frontend renders branching-choice buttons inline in chat. Agent provides narrative context and handles optional free-text follow-ups.
- `save_assessment_response(dimension, sub_dimension, question_id, score)` — save a Likert response (validates `current_phase == 'assessment'`). Primarily used by the direct API endpoint; available to agent for edge cases. Supports `score: null, skipped_reason: "not_applicable"` for inapplicable questions.
- `save_scenario_response(scenario_id, choice, free_text)` — save a behavioral scenario response; `choice` maps deterministically to a quadrant weight, `free_text` is optional qualitative context
- `advance_phase(new_phase, reason)` — move to next phase when assessment complete; validates **applicability-aware completion**: all *applicable* questions answered, at least 60% per dimension answered (below that = "insufficient data"), dimensions with >20% N/A flagged in profile

**Assessment UX flow**:

```text
Agent sends grouping message ("Let's explore your Emotional Awareness...")
  -> Agent calls present_question_batch(question_ids)
    -> SSE event renders LikertBatchCard inline in chat (2-5 questions, clickable radio scales)
    -> User clicks answers -> each click hits POST /api/assessment/responses directly
    -> Backend writes to DB + emits assessment.progress SSE -> Results Panel updates
  -> When batch complete, frontend auto-notifies agent
  -> Agent provides next grouping or transitions to scenarios

Behavioral scenarios:
  -> Agent narrates scenario in chat
  -> Agent calls present_scenario(scenario_id)
    -> SSE event renders ScenarioCard with clickable A/B/C/D buttons
    -> User clicks choice -> POST /api/assessment/responses saves choice + quadrant_weight
  -> If follow_up_prompt exists, agent asks conversationally
  -> User types free-text -> agent calls save_scenario_response() to append
```

**Likert responses bypass the agent** — routing 200 clicks through the LLM would be slow, expensive, and pointless. The agent provides narrative grouping and context; the frontend handles the mechanical input.

**Prompt approach**:
- Group questions by dimension, explain context before each batch
- Behavioral scenarios use **branching-choice format** for reliable classification. Each choice maps deterministically to a quadrant weight. Optional free-text follow-up captures nuance but doesn't affect scoring.
- Can resume where left off (survey state persisted; new sessions bootstrap from `get_assessment_state()`, not conversation replay)
- Proactively offers save points after each dimension: "Good progress — we've finished Emotional Awareness. Want to continue or pick this up later?"
- When a question doesn't apply to the user's context (parenting for non-parents, workplace for retirees), mark N/A. Do not offer "skip" as a general option.

**Time estimate**: 40-55 minutes total across multiple sessions. Breakdown: ~10-13 min Likert clicks, ~4-5 min agent context, ~15-20 min scenarios, ~10-15 min free-text follow-ups.

**Source references**:
- Question bank: `/networks/awareness-framework/questions-template.json` (155 existing questions across 10 dimensions, 31 sub-dimensions — plus ~45 new transmutarian dimension questions)
- New transmutarian dimensions to design: Flow Awareness, Transmutation Capacity, Systemic Awareness (~45 new questions, 3 dims x 3 sub-dims x 5 questions — see `docs/plans/transmute-engine.md`)
- Behavioral scenarios: ~20 new branching-choice scenarios mapping to quadrant archetypes across Maslow levels, informed by Transmutarianism Section 4 (quadrant model) and Section 7 (measurement framework)
- Total assessment scope: ~200 Likert questions + ~20 behavioral scenarios

#### 2. Profile Agent

**Purpose**: Generate and explain the user's awareness + transmutation profile.

**Tools**:

- `get_user_profile()` — full profile data (scores, spider chart data, quadrant placement)
- `generate_profile_snapshot()` — **deterministic computation only** (no parameters — fetches assessment data from DB via closure context): sum Likert responses into dimension/sub-dimension scores, compute spider chart data points, calculate quadrant placement from defined thresholds. Handles `reverse_scored` questions and N/A exclusions. No LLM involvement in scoring — reproducibility is required for progress tracking.
- `save_profile_snapshot(interpretation)` — persist the generated snapshot paired with the LLM's narrative interpretation (synopsis, cross-dimensional insights). Emits `profile.snapshot` SSE event.
- `advance_phase(new_phase, reason)` — validates predicate: profile snapshot generated and presented to user

**Prompt approach**:

- Walk through the spider chart dimension by dimension
- Explain transmutation quadrant placement using their actual scores
- Highlight cross-dimensional relationships: "Your high Emotional Awareness (21/25) but low Emotional Regulation (11/25) means you see the flows but can't transform them yet — that's the Conduit pattern"
- No shame framing — Conduit is morally neutral (Transmutarianism Section 2.2). Never frame a low score as a failure; frame it as untapped capacity.
- Note any dimensions flagged as "insufficient data" (>20% N/A) and explain what that means
- The agent provides the **narrative interpretation** (strengths, weaknesses, cross-dimensional insights) — the tool provides the **deterministic numbers**

**Source references**:
- Snapshot generation pattern: `/networks/awareness-framework/profile-snapshot.py`
- Spider chart: same file, matplotlib radar chart
- Cross-profile findings: all 3 test profiles showed high Internal Self-Awareness (79-82/100), low Emotional Regulation (42-47/75)

#### 3. Education Agent

**Purpose**: Teach transmutarian concepts personalized to the user's profile, with structured comprehension checks.

**Tools**:

- `get_user_profile()` — read profile for personalization
- `get_education_progress()` — which topics covered, comprehension scores per dimension/category
- `record_comprehension_answer(dimension, category, question_id, selected_option)` — deterministic scoring: looks up correct answer from `comprehension_checks.json`, updates `questions_answered` and `questions_correct`, recomputes `understanding_score` as `(correct / answered) * 100`. Returns `{correct: bool, explanation: str, score: int}`. The agent never passes a score — it passes the user's selected option, and the tool does the rest.
- `advance_phase(new_phase, reason)` — validates predicate: top 3 weakest priority dimensions (by profile score) each have >= 60% comprehension score AND all 5 education categories per dimension have at least 1 comprehension question answered

**Prompt approach**:

- 5 education categories per dimension (mirrors WCRP awareness agent's `EducationDimensionAnalysis` pattern):
  1. What this dimension means in transmutarian terms
  2. Your score and what it indicates about your transmutation pattern
  3. How this dimension affects your daily filtering/amplification
  4. Strengths to leverage and gaps to address
  5. How external systems (family, work, culture) interact with this dimension
- Prioritize weakest dimensions first (recency + weakness scoring)
- Teach the core transmutarian vocabulary through their own data, not abstract theory
- **Comprehension checks**: After covering each education category, present 1-2 structured-choice questions from `comprehension_checks.json` (rendered as clickable options in the chat — same `StructuredChoice` component as Likert/scenarios). Question types: `apply_concept`, `identify_pattern`, `predict_outcome` — all scenario-based, testing application not recall. After the user answers, provide the explanation. Optionally ask the `reflection_prompt` for depth — LLM provides qualitative feedback on reflections but this has **zero effect on the score**.

**Comprehension question scope**: 130 total (13 dims x 5 categories x 2 questions per category: 1 foundational + 1 applied). A user encounters ~30 (top 3 weakest dims). First authoring pass: ~30-40 questions.

**Source references**:
- Education state machine: `/networks/awareness_agent_project/src/states/education/state_education_dimension_analysis.py`
- Transmutarian concepts: `transmutarianism_v13.pdf` Sections 1-5 (empirical foundations, introduction, math, quadrant model, AI universality)

#### 4. Development Agent

**Purpose**: Prescribe and track practices that target specific awareness-to-transmutation linkages.

**Tools**:

- `get_user_profile()`
- `get_development_roadmap()` — current 3-step roadmap with practices
- `generate_roadmap()` — create prioritized 3-step plan (no parameters — fetches profile data from DB via closure context) targeting weakest transmutation linkages
- `save_roadmap(roadmap)` — persist and emit `development.roadmap` SSE event
- `update_roadmap(adjustment_reason, retain_practices, drop_practices)` — mid-cycle adjustment: creates a new `development_roadmap` row with `parent_roadmap_id` linking to the original. **Cooldown**: no more than one adjustment per 7 days (enforced in tool). **Scope limit**: can swap individual practices but cannot change targeted dimensions (that requires full reassessment).
- `log_practice_entry(practice_id, reflection, self_rating)` — journal/track practice, emit `development.practice` SSE event
- `get_practice_history(practice_id)` — review past entries
- `advance_phase(new_phase, reason)` — validates predicate: triggers re-assessment after 10 practice entries or 30 days elapsed; transitions to `reassessment` phase

**Prompt approach**:

- Generate roadmap targeting the specific awareness dimension that most limits transmutation capacity (not just lowest score — the one with highest transmutation impact)
- Each roadmap step includes: education, a concrete practice, and a reflective conversation prompt (mirrors `/networks/awareness-framework/roadmap.py` pattern)
- Practices are mapped to transmutation operations: "This breathing exercise targets your deprivation filtering capacity at the belonging level"
- Regenerative focus — build independent capacity, not platform dependency (Transmutarianism Section 6.13)
- **Mid-cycle adjustment**: If a user reports difficulty (3+ entries on the same practice where `self_rating` trends downward), proactively ask if the practice feels unworkable. Also responds to explicit user requests or life changes. Explains what's changing and why.
- Periodic re-assessment prompts to track movement — triggers handoff to `reassessment_agent`

**Source references**:
- Roadmap generation: `/networks/awareness-framework/roadmap.py`
- Kevin's roadmap example: `/networks/awareness-framework/data/kevin/roadmap.json`
- Goal + practice generation: `/networks/awareness_agent_project/src/states/onboarding/state_onboarding_goals.py`
- Regeneration concept: `transmutarianism_v13.pdf` Section 6.13

#### 5. Reassessment Agent

**Purpose**: Run targeted re-assessment after a development cycle, compare before/after profiles, evaluate graduation readiness, and feed updated data back into the development loop.

**Tools**:

- `get_user_profile()` — current profile for comparison baseline
- `get_assessment_state()` — to build on prior assessment data
- `save_assessment_response(dimension, sub_dimension, question_id, score)` — save re-assessment responses (validates `current_phase == 'reassessment'`)
- `present_question_batch(question_ids)` — same as assessment agent, for clickable Likert delivery
- `generate_comparison_snapshot(previous_snapshot_id)` — compute delta scores, movement visualization, quadrant shift
- `save_profile_snapshot(interpretation)` — persist updated profile with LLM narrative
- `evaluate_graduation_readiness()` — checks three convergence indicators (see Graduation below). Returns which indicators are met with evidence.
- `advance_phase(new_phase, reason)` — returns to `development` phase with updated profile, OR transitions to `graduation` if 2-of-3 graduation indicators met

**Prompt approach**:

- Runs a **shorter assessment** — only re-assesses dimensions targeted by the most recent roadmap (not all ~200 questions). Estimated time: 10-15 minutes.
- **Sentinel check-ins**: After targeted reassessment, asks 5 additional sentinel questions from the 2-3 most stale non-targeted dimensions (highest days since last assessed). Picks questions with the most extreme prior scores. Scores via weighted blend: 70% prior assessment + 30% sentinel extrapolation. If sentinel detects >15 point shift, flags that dimension for full reassessment next cycle. No dimension goes more than 2 cycles without at least sentinel coverage; force-include at 3 cycles.
- Generates a comparison view: delta scores, dimension movement arrows, quadrant shift if any
- Celebrates progress without inflating it — "Your Emotional Regulation moved from 42 to 58. That's meaningful movement."
- If no movement detected, explores why non-judgmentally and may suggest roadmap adjustment
- Creates a new `assessment_state` row with `assessment_type: 'reassessment'` (preserves full history for longitudinal tracking)
- After comparison, evaluates graduation readiness. If 2-of-3 indicators met, initiates graduation conversation. If not, mentions progress naturally: "Your scores are stabilizing — that's a good sign for long-term independence."

**Source references**:
- Re-assessment concept: `transmutarianism_v13.pdf` Section 7 (measurement framework)
- Progress tracking pattern: mirrors existing `education_dimension_analysis` recency scoring

#### 6. Graduation Agent

**Purpose**: Execute the closing sequence when graduation criteria converge, generate longitudinal artifacts, and transition to graduated state.

**Graduation criteria — convergence signal (any 2 of 3)**:

| Indicator | What It Measures | Trigger Condition |
|---|---|---|
| **Pattern Stability** | Scores stopped meaningfully changing | Delta across all targeted dimensions < 5% for two consecutive reassessment cycles |
| **Quadrant Consolidation** | Transmutation pattern has settled | Same quadrant placement for two consecutive reassessments |
| **Self-Assessed Readiness** | User believes they can continue independently | User explicitly indicates readiness when prompted |

**What is explicitly NOT a graduation criterion**: Reaching the Transmuter quadrant (Conduit is a valid stabilization point), a minimum score on any dimension, or a time-based deadline.

**Tools**:
- `get_longitudinal_snapshots()` — all profile snapshots for timeline view
- `generate_graduation_artifacts()` — creates practice map, pattern narrative data
- `save_graduation_record(pattern_narrative)` — persist graduation data with LLM narrative
- `advance_phase('graduated', reason)`

**Closing sequence**:
1. **Longitudinal Review** — first snapshot vs. current, all intermediate reassessments, dimension-by-dimension movement
2. **Pattern Narrative** — LLM synthesizes the user's journey into a personalized narrative
3. **Independent Practice Map** — reference document: which practices worked, which dimensions to watch, specific vulnerabilities
4. **Graduation Snapshot** — final profile snapshot marked `assessment_type: 'graduation'`
5. **Check-In Invitation** — explicit framing that the door remains open

**Avoiding perverse incentives**: Graduation requires *stable* scores, not high scores — gaming inflates to a stable plateau. Agent never initiates graduation unprompted by indicators. No countdown or progress bar. Re-entry framed as maintenance, not failure.

#### 7. Check-In Agent

**Purpose**: Post-graduation periodic assessment, compare against graduation baseline.

**Tools**:
- `get_graduation_record()` — graduation baseline data
- `get_assessment_state()`, `save_assessment_response()`, `present_question_batch()` — for full reassessment
- `generate_comparison_snapshot(graduation_snapshot_id)` — compare against graduation baseline
- `save_check_in_log()` — record check-in results
- `advance_phase('graduated', reason)` (default) or `advance_phase('development', reason)` (re-entry if significant regression)

**Behavior**:
- Runs **full reassessment** (all dimensions), not targeted
- Compares against graduation snapshot, not just previous snapshot
- If significant regression (>15% drop in previously-targeted dimensions), surfaces it without alarm and offers re-entry
- Default return to `graduated` state
- Suggested cadence: 3 months, 6 months, then annually. Suggestions only — no push notifications.

### Transmutation Quadrant Model

The quadrant model maps a user's transmutation pattern along two axes:

- **X-axis**: Deprivation handling (Filter <-> Amplify)
- **Y-axis**: Fulfillment handling (Absorb <-> Emit)

Quadrant placement:

- **Transmuter** = Filter deprivation + Emit fulfillment (top-left) — breaks cycles, amplifies good
- **Magnifier** = Amplify deprivation + Emit fulfillment (top-right) — amplifies everything outward
- **Absorber** = Filter deprivation + Absorb fulfillment (bottom-left) — internalizes everything
- **Extractor** = Amplify deprivation + Absorb fulfillment (bottom-right) — takes from others
- **Conduit** = Center (passthrough on both axes) — default processing, morally neutral baseline

Quadrant placement is computed **deterministically** from transmutation dimension scores and behavioral scenario weights using defined thresholds. The profile agent interprets placement narratively; the tool computes placement mathematically.

---

## Data Model (SQLite)

### Schema

```sql
-- Version tracking for migrations
schema_version
  version         INTEGER PRIMARY KEY
  applied_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP

users
  id              TEXT PRIMARY KEY
  name            TEXT NOT NULL
  email           TEXT UNIQUE NOT NULL
  password_hash   TEXT NOT NULL
  current_phase   TEXT DEFAULT 'orientation'
                  -- orientation | assessment | profile | education | development
                  -- | reassessment | graduation | graduated | check_in
  graduated_at    TIMESTAMP  -- NULL until graduation
  created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP

assessment_state
  id              TEXT PRIMARY KEY
  user_id         TEXT FK
  assessment_type TEXT    -- 'initial' | 'reassessment' | 'check_in' | 'graduation'
  responses       JSON    -- {dimension: {sub_dimension: {question_id: {score, skipped_reason?}}}}
  scenario_responses JSON -- {scenario_id: {choice, quadrant_weight, free_text}}
  completed_at    TIMESTAMP
  created_at      TIMESTAMP

profile_snapshots
  id              TEXT PRIMARY KEY
  user_id         TEXT FK
  assessment_id   TEXT FK  -- links to assessment_state.id for longitudinal tracking
  snapshot        JSON    -- dimension scores, sub-dimension scores, quadrant placement (all deterministic)
  interpretation  JSON    -- LLM-generated narrative synopsis, cross-dimensional insights
  spider_chart    BLOB    -- PNG
  previous_snapshot_id TEXT FK -- for comparison deltas (NULL for first snapshot)
  created_at      TIMESTAMP

education_progress
  user_id         TEXT FK
  progress        JSON
  -- {dimension: {category: {
  --   understanding_score: 0-100,    (deterministic: correct/answered * 100)
  --   questions_answered: [ids],
  --   questions_correct: [ids],
  --   last_discussed: epoch,
  --   reflection_given: bool
  -- }}}

development_roadmap
  id              TEXT PRIMARY KEY
  user_id         TEXT FK
  parent_roadmap_id TEXT FK  -- NULL for initial, links to prior roadmap for mid-cycle adjustments
  roadmap         JSON    -- 3-step plan with practices
  created_at      TIMESTAMP

practice_journal
  id              TEXT PRIMARY KEY
  user_id         TEXT FK
  practice_id     TEXT
  reflection      TEXT
  self_rating     INTEGER
  created_at      TIMESTAMP

graduation_record
  id              TEXT PRIMARY KEY
  user_id         TEXT FK
  final_snapshot_id   TEXT FK
  initial_snapshot_id TEXT FK
  practice_map        JSON     -- independent practice reference document
  pattern_narrative   TEXT     -- LLM-generated journey narrative
  graduation_indicators JSON   -- which 2 of 3 criteria were met, with evidence
  created_at          TIMESTAMP

check_in_log
  id              TEXT PRIMARY KEY
  user_id         TEXT FK
  snapshot_id     TEXT FK
  graduation_snapshot_id TEXT FK
  regression_detected    BOOLEAN
  re_entered_development BOOLEAN DEFAULT FALSE
  created_at      TIMESTAMP

safety_log
  id              TEXT PRIMARY KEY
  user_id         TEXT FK
  reason          TEXT
  created_at      TIMESTAMP

adk_sessions
  user_id         TEXT
  session_id      TEXT
  app_name        TEXT
  session_state   TEXT    -- JSON: current session conversation history + ephemeral state vars
  archived        BOOLEAN DEFAULT FALSE  -- prior sessions archived when new session starts
  total_input_tokens  INTEGER DEFAULT 0
  total_output_tokens INTEGER DEFAULT 0
  estimated_cost_usd  REAL DEFAULT 0.0
  updated_at      TIMESTAMP
```

### Session State Lifecycle

- Each login/sitting creates a new ADK session. Prior sessions marked `archived = true`.
- `session_state` stores only the **current session's** conversation history + ephemeral vars (e.g., `last_dimension`, `questions_this_session`). Expected size: 100-300 KB per session.
- Domain state lives in domain tables (`assessment_state`, `profile_snapshots`, etc.), not in session state. New sessions bootstrap from `get_assessment_state()` / `get_user_profile()`.
- No mid-session truncation. Let conversation grow naturally within a sitting.

### DB Migration Strategy

Numbered SQL files with a version table:
- `schema_version` table tracks applied migrations
- Migration files in `db/migrations/`: `001_initial.sql`, `002_add_archived_column.sql`, etc.
- On startup, `database.py` checks highest applied version, runs unapplied migrations in order (~30 lines of Python)
- `db/reset.sh` provided for clean start during development
- Replaced by Alembic when porting to WCRP

### WCRP Mapping

- `assessment_state` + `profile_snapshots` → equivalent of `preparation_sessions.conflict_summary`
- `adk_sessions` → equivalent of `preparation_sessions.adk_session_state`
- `practice_journal`, `graduation_record`, `check_in_log` → new, but follow same JSONB pattern

---

## Shared Prompt Modules (mirrors WCRP's `prompts/shared/`)

| Module | Purpose | ~Tokens |
| --- | --- | --- |
| `safety.py` | Mental health escalation — three-tier response: mild (acknowledge + continue), moderate (de-escalate + offer pause), severe (crisis resources + stop assessment + flag via `flag_safety_concern()` tool). Agent never attempts to counsel. | ~200 |
| `boundary.py` | Keep each sub-agent in its lane. Off-topic: acknowledge and redirect. After 3+ consecutive off-topic messages: offer to pause and resume later. | ~150 |
| `no_shame.py` | The Conduit baseline framing — you're not broken, you're running default processing (Section 2.2). "Never frame a low score as a failure. Frame it as untapped capacity." If user disputes a score: validate their perspective, explain what the data reflects, offer to re-answer. | ~200 |
| `transmutation_concepts.py` | Core transmutarian vocabulary and definitions — agent reference material drawn from `transmutarianism_v13.pdf` Sections 2-4. Includes the quadrant model geometry. | ~500 |
| `awareness_dimensions.py` | The 10+3 dimension definitions (10 awareness + Flow Awareness, Transmutation Capacity, Systemic Awareness) and what they mean for transmutation capacity | ~400 |
| `orientation.py` | Root agent orientation instructions — consent/readiness exchange, grounding question, when to advance | ~200 |

**Prompt composition order** (each sub-agent's `system_instruction`):

```python
system_instruction = (
    safety.PROMPT                     # ~200 tokens, always included
    + boundary.PROMPT                 # ~150 tokens, always included
    + no_shame.PROMPT                 # ~200 tokens, always included
    + transmutation_concepts.PROMPT   # ~500 tokens, always included
    + awareness_dimensions.PROMPT     # ~400 tokens, always included
    + agent_specific_prompt           # ~500-800 tokens, per sub-agent
)
# Total: ~1,500-2,300 tokens for system instruction
```

---

## Web Interface

### Layout: Two-Panel Design (mirrors WCRP's agent UI)

```text
+-------------------------------------------------------------------+
|  Transmutation Engine          [Cost: $0.12] [User Name] [Logout] |
+-------------------------------+-----------------------------------+
|                               | [Assessment|Profile|Education|Dev] |
|  Chat Window                  |                                   |
|                               |  +- Profile Summary ------------+ |
|  Agent: Welcome, Kevin.       |  | Phase: Assessment (active)    | |
|  Before we start, did the     |  | Progress: 32/~200 questions   | |
|  overview make sense?         |  |                               | |
|                               |  | [Spider Chart]                | |
|  You: Yes, I'm curious        |  | (updates as scores come in)   | |
|  about my patterns...         |  |                               | |
|                               |  | Quadrant: TBD                 | |
|  Agent: Great. Let's          |  +-------------------------------+ |
|  explore your Emotional       |                                   |
|  Awareness...                 |  +- Current Dimension ----------+ |
|                               |  | Emotional Regulation          | |
|  +-------------------------+  |  | ██████░░░░░░ 11/25            | |
|  | Emotion Recognition     |  |  |                               | |
|  |                         |  |  | Impulse Control               | |
|  | 1. I can usually        |  |  | █████░░░░░░░ 13/25            | |
|  |    identify what I'm    |  |  +-------------------------------+ |
|  |    feeling...           |  |                                   |
|  |    o SD  o D  o N  o A  |  |  [Download My Data]               |
|  |    o SA                 |  |  Est. cost: $0.12 | Total: $2.45  |
|  +-------------------------+  |                                   |
|                               |                                   |
|  +------------------------+   |                                   |
|  | Type a message...      |   |                                   |
|  +------------------------+   |                                   |
+-------------------------------+-----------------------------------+
|  Sessions: [New] [Mar 5 - Assessment] [Mar 12 - Education]       |
+-------------------------------------------------------------------+
```

**Left panel**: Chat window — streaming agent responses via SSE. Includes inline interactive widgets: `LikertBatchCard` (clickable radio scales), `ScenarioCard` (clickable A/B/C/D buttons), `StructuredChoice` (comprehension check options). All use the same component pattern.

**Right panel**: Results — updates in real-time via SSE events. Shows **tabs based on data existence** (not just `current_phase`), so users can always review past phase data:

- **Orientation phase**: Static orientation content (what is transmutarianism, what to expect)
- **Assessment phase** (tab visible when `assessment_state` exists): Progress tracker, per-dimension completion bars, partial spider chart
- **Profile phase** (tab visible when `profile_snapshots` exists): Full spider chart (neutral blue gradient, not red/green), quadrant placement, dimension breakdown
- **Education phase** (tab visible when `education_progress` exists): Priority dimensions, comprehension scores per dimension/category, current topic
- **Development phase** (tab visible when `development_roadmap` exists): Roadmap, practice log, progress over time
- **Reassessment phase** (tab visible when comparison snapshots exist): Comparison view — delta scores, movement arrows, quadrant shift
- **Graduation phase**: Longitudinal timeline (all snapshots overlaid), quadrant trajectory, journey narrative, practice map
- **Graduated phase**: Summary card, graduation date, final quadrant, "Schedule Check-In" prompt

Active phase tab visually distinguished (bold label, accent color). `current_phase` controls which tools can write; tabs are read-only views of existing data.

Spider chart uses neutral colors and labels axes as "capacity levels" rather than raw scores. Shown alongside the quadrant map so the user sees *pattern* not *deficit*.

**Bottom bar**: Session list — user can have multiple sessions. Clicking a past session loads its conversation but does NOT change the current phase.

**Results Panel footer**: "Download My Data" button (visible in all phases) + session cost widget.

### Agent Handoff UX

When the root agent routes between sub-agents:
1. Current sub-agent wraps up with a summary message
2. System message appears in chat (gray italic): "Phase transition: Assessment -> Profile"
3. Results Panel updates via `phase.transition` SSE event — swaps to appropriate tab
4. New sub-agent introduces itself with context

Phase indicator in Results Panel header with colored dot (blue=assessment, green=profile, purple=education, orange=development, teal=reassessment).

### SSE Event Schema

**Chat streaming** (emitted during `POST /chat/{session_id}` response):

```text
event: agent.thinking        data: {}
event: agent.message.chunk   data: {"text": "That's a really "}
event: agent.message.chunk   data: {"text": "important insight. "}
event: tool.call             data: {"tool": "save_assessment_response", "args": {...}}
event: tool.result           data: {"tool": "save_assessment_response", "result": "saved"}
event: agent.message.complete data: {"full_text": "..."}
event: error                 data: {"code": "model_error", "message": "..."}
```

**Domain events** (emitted by tool functions during the chat stream):

```json
{"event": "phase.transition", "data": {"from": "assessment", "to": "profile"}}
{"event": "assessment.question_batch", "data": {"batch_id": "...", "sub_dimension": "...", "dimension": "...", "questions": [{"id": "...", "text": "...", "scale_type": "...", "scale_labels": [...]}]}}
{"event": "assessment.scenario", "data": {"scenario_id": "...", "dimension": "...", "narrative": "...", "choices": [{"key": "a", "text": "..."}], "has_follow_up": true}}
{"event": "assessment.progress", "data": {"answered": 32, "total": 200, "current_dimension": "...", "dimension_progress": {"Emotional Awareness": {"answered": 12, "total": 15, "avg_score": 3.8}}, "scenarios_completed": 3, "scenarios_total": 20}}
{"event": "profile.snapshot", "data": {"spider_data": {}, "quadrant": "Conduit", "synopsis": "..."}}
{"event": "education.progress", "data": {"dimension": "...", "category": "...", "understanding": 72}}
{"event": "education.comprehension", "data": {"dimension": "...", "category": "...", "question_id": "...", "correct": true, "score": 67, "categories_covered": 4, "categories_total": 5}}
{"event": "development.roadmap", "data": {"steps": [], "current_step": 1}}
{"event": "development.practice", "data": {"practice_id": "...", "entry_count": 5, "last_entry": "..."}}
{"event": "graduation.readiness", "data": {"indicators_met": 2, "details": {...}}}
{"event": "graduation.complete", "data": {"graduation_record_id": "...", "longitudinal_data": {...}}}
{"event": "checkin.complete", "data": {"comparison": {...}, "regression_detected": false}}
{"event": "session.cost", "data": {"session_cost_usd": 0.12, "total_cost_usd": 2.45, "session_tokens": {"input": 15234, "output": 7891}}}
```

Note: `assessment.scenario` omits `quadrant_weight` from choices — scoring data the frontend doesn't need.

### Frontend Tech

Keep it simple — this is a local tool, not a production SaaS frontend:

- **Option A (recommended)**: Plain HTML/CSS/JS served by FastAPI's `StaticFiles` — zero build step, zero Node dependency. Use `EventSource` for SSE. Vanilla JS or Alpine.js for reactivity.
- **Option B**: Lightweight React/Preact SPA — if we want component structure for easier porting to WCRP's Next.js later.

Recommend **Option A** for MVP — faster to build, no frontend build pipeline, and the WCRP port will rewrite the frontend anyway (Next.js App Router + BFF pattern). The API contract is what matters for portability, not the frontend code.

---

## Chat API Contract

### Session Management

```text
POST /sessions           — {} (user_id from cookie) -> {"session_id": "uuid", "created_at": "..."}
GET  /sessions           — list user sessions with metadata (excludes archived by default)
```

### Chat Endpoint

POST returns an SSE stream (same pattern as WCRP's `run_agent_with_sse()`):

```text
POST /chat/{session_id}
Content-Type: application/json
Request:  {"message": "I tend to notice when I'm frustrated but..."}
Response: text/event-stream (SSE)
```

The connection stays open until the agent's full turn completes. Domain events (`assessment.progress`, `profile.snapshot`, etc.) are emitted by tool functions during the same stream.

### Assessment Response Endpoints (Direct API, Bypass Agent)

```text
GET  /api/assessment/questions             — full question bank JSON (cached)
GET  /api/assessment/questions/{dimension} — questions for a specific dimension
POST /api/assessment/responses             — {question_id, type, score?, choice_key?, quadrant_weight?}
POST /api/assessment/responses/batch       — {responses: [{question_id, score}...]}
GET  /api/assessment/state                 — current progress, per-dimension completion
```

### Results & Export

```text
GET  /api/results/{user_id}  — all completed phase data (for Results Panel tabs)
GET  /export/{user_id}       — JSON file download of all user data (session cookie protected)
```

---

## User Management (Local)

Simple local auth — meant for running on a home/office network, not the public internet.

### Features

- **Create account**: Name, email, password (bcrypt hashed). No email verification.
- **Login**: Email + password -> session cookie (signed, httponly)
- **Session persistence**: Cookie-based sessions stored in SQLite
- **Multiple users**: Anyone on the local network can create an account and use the system independently
- **No roles/permissions**: Every user is equal. No admin panel.
- **No OAuth, no MFA, no invitations**: Those come with the WCRP port

### Auth API Endpoints

```text
POST /auth/register     — { name, email, password } -> set session cookie
POST /auth/login        — { email, password } -> set session cookie
POST /auth/logout       — clear session cookie
GET  /auth/me           — current user info (from session cookie)
```

### Rate Limiting

`slowapi` middleware (wraps `limits`, integrates with FastAPI). In-memory storage, no Redis.

| Endpoint | Limit | Key |
|---|---|---|
| `POST /auth/register` | 5/hour | IP address |
| `POST /auth/login` | 10/minute | IP address |
| `POST /chat/{session_id}` | 30/minute | User ID |
| `GET /export/{user_id}` | 5/hour | User ID |

### Porting Note

When moving to WCRP-shell, this entire auth layer gets deleted and replaced by WCRP's JWT + OAuth system. The `users` table maps to WCRP's tenant `users` model. The `user_id` FK pattern is identical — only the auth mechanism changes.

---

## Running

```bash
# Clone and configure
git clone <repo>
cp .env.example .env
# Edit .env: set ANTHROPIC_API_KEY (or OPENAI_API_KEY, or BEDROCK_* vars)

# Run directly
pip install -r requirements.txt
python main.py
# -> Starts FastAPI on http://localhost:54718

# Or via Docker
docker compose up
# -> Backend on :54718, serves frontend at /
```

Open `http://localhost:54718` in a browser. Create an account. Start chatting with the agent. Share the URL with others on your network (`http://<your-ip>:54718`).

---

## Model Configuration

```yaml
# config.yaml
model:
  provider: anthropic          # anthropic | openai | bedrock | ollama
  model_id: claude-sonnet-4-5-20250514  # provider-specific model ID
  api_key_env: ANTHROPIC_API_KEY  # env var name containing the key

# For Bedrock (mirrors WCRP's env vars):
# BEDROCK_AWS_ACCESS_KEY_ID, BEDROCK_AWS_SECRET_ACCESS_KEY, BEDROCK_AWS_REGION

# Cost tracking (per 1M tokens)
model_costs:
  claude-sonnet-4-5-20250514: { input: 3.00, output: 15.00 }
  gpt-4o: { input: 2.50, output: 10.00 }
  gpt-4o-mini: { input: 0.15, output: 0.60 }
  ollama/*: { input: 0.00, output: 0.00 }
```

Uses ADK's `LiteLlm` adapter — same as WCRP. Any LiteLLM-supported provider works.

**Estimated cost per full user journey** (assessment through development):

| Model | Est. Cost |
|---|---|
| Claude Sonnet 4.5 | ~$4.20 |
| GPT-4o | ~$3.00 |
| GPT-4o-mini | ~$0.18 |
| Ollama (local) | $0.00 |

---

## File Structure

```text
transmute-engine/
├── main.py                     # Entry point — FastAPI server
├── config.yaml                 # Model + runtime + cost config
├── .env.example                # Template for API keys
├── Dockerfile
├── docker-compose.yml
├── requirements.txt            # google-adk, litellm, fastapi, bcrypt, sqlite3, slowapi
├── agents/
│   └── transmutation/
│       ├── agent.py            # create_transmutation_agent() — root + sub-agents
│       ├── tools.py            # Closure-injected tools (create_transmutation_tools())
│       ├── session_service.py  # SqliteSessionService (extends BaseSessionService)
│       ├── question_bank.py    # Loads/indexes questions.json + comprehension_checks.json
│       ├── sub_agents/
│       │   ├── assessment.py
│       │   ├── profile.py
│       │   ├── education.py
│       │   ├── development.py
│       │   ├── reassessment.py
│       │   ├── graduation.py
│       │   └── check_in.py
│       └── prompts/
│           ├── shared/
│           │   ├── safety.py
│           │   ├── transmutation_concepts.py
│           │   ├── awareness_dimensions.py
│           │   ├── no_shame.py
│           │   ├── boundary.py
│           │   └── orientation.py
│           ├── assessment_prompt.py
│           ├── profile_prompt.py
│           ├── education_prompt.py
│           ├── development_prompt.py
│           ├── reassessment_prompt.py
│           ├── graduation_prompt.py
│           └── check_in_prompt.py
├── api/
│   ├── auth.py                 # Register, login, logout, me
│   ├── chat.py                 # POST /chat/{session_id} -> SSE stream
│   ├── sessions.py             # CRUD for user sessions
│   ├── assessment.py           # Assessment response endpoints (direct API, bypass agent)
│   ├── results.py              # GET results data for Results Panel tabs
│   └── export.py               # GET /export/{user_id} -> JSON download
├── frontend/
│   ├── index.html              # Main app shell
│   ├── content/
│   │   └── orientation.html    # Static orientation content for Results Panel
│   ├── css/
│   │   └── app.css
│   └── js/
│       ├── app.js              # Main app logic
│       ├── chat.js             # Chat window + SSE listener
│       ├── results.js          # Results panel (tabbed: spider chart, scores, roadmap, etc.)
│       ├── auth.js             # Login/register forms
│       └── components/
│           ├── likert-card.js  # LikertBatchCard — inline clickable radio scales
│           ├── scenario-card.js # ScenarioCard — inline clickable A/B/C/D buttons
│           └── structured-choice.js  # Reusable for comprehension checks
├── data/
│   ├── questions.json          # ~200 Likert questions + ~20 behavioral scenarios (unified schema)
│   ├── comprehension_checks.json  # ~130 structured comprehension questions for education phase
│   └── transmutarianism_v13.pdf
├── db/
│   ├── database.py             # SQLite setup + migration runner
│   ├── reset.sh                # Delete DB + restart for clean development
│   └── migrations/
│       ├── 001_initial.sql
│       └── ...
└── docs/
    └── plans/
        ├── transmute-engine.md
        ├── transmute-standalone-mvp.md
        └── mvp-design-resolutions.md
```

---

## Porting Checklist (MVP -> WCRP-Shell)

When it's time to build the full SaaS platform:

1. **Copy `agents/transmutation/`** into WCRP's `apps/backend/app/agents/`
2. **Swap `SqliteSessionService`** -> `PostgresSessionService` (same `BaseSessionService` interface)
3. **Swap tool closures**: `get_db_session()` -> `get_tenant_session(tenant_schema)`
4. **Port FastAPI routes** (`api/`) into WCRP's `apps/backend/app/api/v1/` pattern
5. **Port SSE events** into WCRP's `run_agent_with_sse()` background task pattern
6. **Add SQLAlchemy models** for tenant schema (mirror SQLite tables including new: `graduation_record`, `check_in_log`, `safety_log`)
7. **Add Alembic migrations** for new tenant tables (replaces numbered SQL files)
8. **Rebuild frontend** in Next.js App Router with BFF proxy (reuse layout/UX including tabbed Results Panel, rewrite code)
9. **Delete local auth** — replace with WCRP's JWT + OAuth + MFA system
10. **Wire up model selection** from `public.llm_models` table instead of config file
11. **Add token tracking** via `record_llm_usage()` pattern (extend existing `session.cost` tracking)
12. **Port rate limiting** to WCRP's middleware pattern (replace `slowapi` with production solution)

Prompts, sub-agents, and tool logic require **zero changes**.

---

## What Needs To Be Created (New Content)

| Item | Status | Notes |
| --- | --- | --- |
| 155 awareness survey questions | **Exists** | `/networks/awareness-framework/questions-template.json` — port to unified `questions.json` schema with `id`, `type`, `dimension`, `sub_dimension`, `scale_type`, `reverse_scored`, `tags` |
| 3 new transmutarian dimensions | **New** | Flow Awareness, Transmutation Capacity, Systemic Awareness. ~45 new questions (3 dims x 3 sub-dims x 5 questions). Define sub-dimension structure explicitly. |
| Behavioral scenarios | **New** | ~20 branching-choice scenarios in unified `questions.json`. Each with `narrative`, `choices` (key, text, quadrant_weight), optional `follow_up_prompt`. Map to quadrant archetypes across Maslow levels. |
| Comprehension check questions | **New** | ~130 structured-choice questions in `comprehension_checks.json` (13 dims x 5 categories x 2 questions). Types: `apply_concept`, `identify_pattern`, `predict_outcome`. Each with `correct_option`, `explanation`, optional `reflection_prompt`. Author ~30-40 in first pass. |
| Quadrant model geometry + scoring | **New** | 2-axis model (deprivation filtering x fulfillment emission), 5 archetypes, deterministic placement algorithm. Handles N/A dimension exclusions. |
| Orientation content | **New** | Static HTML for Results Panel: what is transmutarianism, what to expect, time/data commitments |
| Transmutation concept prompt modules | **New** | Distill from `transmutarianism_v13.pdf`. Include quadrant model geometry in `transmutation_concepts.py`. |
| Safety/escalation prompts | **Adapt** | Copy from WCRP, adjust for self-awareness context. Add three-tier response definitions. Add `flag_safety_concern()` tool. |
| Sub-agent system prompts (7 agents) | **New** | Follow prompt composition stack. Includes graduation + check-in agents. |
| Profile snapshot generation logic | **Rewrite** | Port from `/networks/awareness-framework/profile-snapshot.py` but make scoring **deterministic** (code, not LLM). Handle `reverse_scored`, N/A exclusions, "insufficient data" flagging. LLM generates narrative interpretation only. |
| Roadmap generation logic | **Adapt** | Port from `/networks/awareness-framework/roadmap.py`. Support mid-cycle adjustments via `parent_roadmap_id`. |
| Spider chart generation | **Adapt** | Port from `/networks/awareness-framework/profile-snapshot.py`. Neutral blue gradient, capacity framing. Consider two-ring chart for 10+3 dimensions. |
| Phase completion predicates | **New** | Define validation logic for each `advance_phase()` transition. Applicability-aware completion for assessment. Deterministic comprehension scoring for education. Convergence-based graduation. |
| SSE event schema + emission | **New** | Full schema defined above. Wire into tool functions + chat streaming. |
| Frontend interactive components | **New** | `LikertBatchCard`, `ScenarioCard`, `StructuredChoice` — reusable inline chat widgets. Tabbed Results Panel with data-existence-based tab visibility. |
| Graduation artifacts | **New** | Longitudinal timeline view, practice map generation, pattern narrative template |
| Sentinel question selection logic | **New** | Staleness calculation, extreme-score selection, weighted blend scoring for reassessment check-ins |
