# MVP Design Resolutions

Tracking document for issues identified during simulation review of `transmute-standalone-mvp.md`.
Each section contains the issue, proposed resolution, and status.

---

## 1. Pre-Assessment Orientation

**Issue**: User gets dropped into assessment with no context about transmutarianism, the quadrant model, or what they'll get out of it. Education phase comes *after* assessment.

**Status**: DRAFTED

**Resolution**:

### Delivery Model: Hybrid — Static Content + Agent-Driven Readiness Check

When a new user first enters the app (or an existing user whose `current_phase` is still `orientation`), the flow is:

**Step 1 — Static orientation page** (rendered in the Results Panel, not the chat). Covers three blocks:

- **What is transmutarianism?** (~3 paragraphs) — Everyone processes deprivation and fulfillment. Most of us pass it through unchanged — that's the Conduit pattern, and it's morally neutral. Transmutarianism maps *how* you process what flows through you across two axes: do you filter or amplify deprivation? Do you absorb or emit fulfillment? This creates five patterns: Transmuter, Magnifier, Absorber, Extractor, and Conduit. None of these are identities — they're current operating patterns you can shift.

- **What you'll do here** (~2 paragraphs) — You'll take an assessment (~200 awareness questions delivered in small conversational batches + ~20 behavioral scenarios). The agent will group questions by topic and give you context as you go. After the assessment, you'll get a profile (spider chart + quadrant placement), personalized education through your own data, and a development roadmap with concrete practices. The whole thing is designed so you need it less over time, not more.

- **What to expect** — Time: the assessment spans multiple sessions (estimate 45-75 minutes total, broken up however you want). You can stop and resume at any point. Your data is stored locally in SQLite on whatever machine is running this app. No data leaves your network except LLM API calls to your configured provider (the content of your chat responses is sent to the LLM for processing). There is no telemetry.

**Step 2 — Consent + readiness check** (agent-driven, in the chat panel). Once the user has the static content visible, the agent opens with a short exchange:

- Confirms they've read the orientation ("Before we start, did the overview in the panel make sense? Any questions about what we'll be doing?")
- Asks one grounding question: "In a sentence or two — what brought you here? What are you hoping to learn about yourself?" This serves two purposes: (a) it gives the agent a motivation anchor to reference during the assessment, and (b) it validates the user is oriented enough to proceed.
- The agent does NOT teach transmutarianism in depth here — that's the education phase's job. The goal is just enough context that the assessment questions aren't confusing.
- When ready, the agent calls `advance_phase('assessment', 'orientation complete')` and hands off to the assessment agent.

**Duration**: 2-5 minutes. The static content is a 90-second read. The agent exchange is 2-3 turns.

### Architecture

- **No new sub-agent**. Orientation is the root agent's responsibility. The root agent handles the consent/readiness exchange directly before routing to `assessment_agent`.
- **Static content** lives in `frontend/content/orientation.html` (or a JSON blob served by `GET /api/orientation`). The frontend renders it in the Results Panel when `current_phase == 'orientation'`.
- **New shared prompt module**: `agents/transmutation/prompts/shared/orientation.py` (~200 tokens). Gives the root agent instructions for the consent/readiness exchange.

### Phase Model Change

- Add `orientation` as the initial phase: `orientation → assessment → profile → education → development → reassessment`
- `users.current_phase` default changes from `'assessment'` to `'orientation'`
- `advance_phase('assessment', reason)` from the `orientation` phase has a trivial predicate: user has sent at least one message.

### File Structure Additions

```
agents/transmutation/prompts/shared/orientation.py   # Root agent orientation prompt (~200 tokens)
frontend/content/orientation.html                     # Static orientation content for Results Panel
```

### What This Does NOT Include (MVP Scope)

- No video or interactive tutorial. Text is fine.
- No quiz or comprehension gate on the orientation content.
- No detailed explanation of the math, Maslow mapping, or scoring methodology (that's education phase territory).
- No separate "terms of service" or formal consent flow. The agent's conversational check is sufficient for a local-network MVP.

---

## 2. Likert as Programmatic UI + JSON-Driven Questions

**Issue**: Typing "4" in chat for 200 questions is slow and awkward. Questions should be rendered as clickable UI elements driven by a JSON question bank, not delivered purely through chat.

**Status**: DRAFTED

**Resolution**:

### Hybrid UX Model: Chat-Orchestrated, UI-Rendered

The agent remains the narrator and grouping mechanism; the frontend renders clickable question widgets instead of expecting typed numeric responses.

**Flow per question batch:**

1. **Agent sends a grouping message** via chat: "Let's explore your Emotional Awareness. These questions are about how you notice and identify your emotions in daily life."
2. **Agent calls** `present_question_batch(question_ids)` which emits an SSE event containing the question data (pulled from the JSON bank).
3. **Frontend receives the SSE event** and renders an inline question card in the chat stream — not in the Results Panel. Each card shows the question text and a row of clickable Likert buttons (e.g., five circles labeled "Strongly Disagree" through "Strongly Agree"). Questions are presented 2-5 at a time.
4. **User clicks answers.** Each click immediately sends the response to `POST /api/assessment/responses`, which writes to the DB and emits an `assessment.progress` SSE event.
5. **Once all questions in the batch are answered**, the frontend sends a lightweight signal to the agent indicating batch completion. The agent then provides the next grouping message or transitions to behavioral scenarios.

**Behavioral scenarios remain conversational.** The agent presents the scenario narrative in chat, renders the branching choices as clickable buttons via an `assessment.scenario` SSE event, and optionally prompts for free-text follow-up.

**Why inline in chat, not the Results Panel:** The question cards appear in the conversation flow so the agent's contextual framing sits directly above the questions. The Results Panel remains dedicated to aggregate progress.

### JSON Question Bank Schema

**File:** `data/questions.json`

```json
{
  "version": "1.0",
  "meta": {
    "total_likert": 200,
    "total_scenarios": 20,
    "dimensions": 13,
    "scale_types": {
      "agreement_5": {
        "points": 5,
        "labels": ["Strongly Disagree", "Disagree", "Neutral", "Agree", "Strongly Agree"]
      },
      "frequency_5": {
        "points": 5,
        "labels": ["Never", "Rarely", "Sometimes", "Often", "Always"]
      }
    }
  },
  "questions": [
    {
      "id": "ea_rec_01",
      "type": "likert",
      "dimension": "Emotional Awareness",
      "sub_dimension": "Emotion Recognition",
      "text": "I can usually identify what specific emotion I am feeling in the moment.",
      "scale_type": "agreement_5",
      "order": 1,
      "reverse_scored": false,
      "tags": ["core"],
      "source": "awareness-framework-v1"
    }
  ],
  "scenarios": [
    {
      "id": "sc_belong_01",
      "type": "scenario",
      "dimension": "Transmutation Capacity",
      "sub_dimension": "Deprivation Filtering",
      "maslow_level": "belonging",
      "narrative": "Your coworker is struggling with their workload and comes to you visibly stressed...",
      "choices": [
        {
          "key": "a",
          "text": "Listen deeply and help them process what they're feeling",
          "quadrant_weight": {"transmuter": 1.0}
        },
        {
          "key": "b",
          "text": "Take on their stress as your own — you can't help but feel it too",
          "quadrant_weight": {"absorber": 1.0}
        },
        {
          "key": "c",
          "text": "Offer quick advice and move on to your own work",
          "quadrant_weight": {"conduit": 1.0}
        },
        {
          "key": "d",
          "text": "Feel a sense of relief or satisfaction that it's them, not you",
          "quadrant_weight": {"extractor": 1.0}
        }
      ],
      "follow_up_prompt": "Can you think of a specific time this happened? What did you actually do?",
      "order": 1,
      "tags": ["interpersonal", "belonging"],
      "source": "transmutarianism-v13-s4"
    }
  ]
}
```

**Likert question fields:**

| Field | Type | Required | Purpose |
|---|---|---|---|
| `id` | string | yes | Unique identifier, prefixed by dimension abbreviation |
| `type` | `"likert"` | yes | Discriminator for rendering |
| `dimension` | string | yes | One of the 13 dimensions |
| `sub_dimension` | string | yes | Sub-dimension within the dimension |
| `text` | string | yes | The question displayed to the user |
| `scale_type` | string | yes | Key into `meta.scale_types` — determines labels and point count |
| `order` | integer | yes | Presentation order within its sub-dimension |
| `reverse_scored` | boolean | yes | If true, scoring inverts (5->1, 4->2, etc.) |
| `tags` | string[] | no | For filtering, grouping, reassessment targeting |
| `source` | string | no | Provenance tracking |

**Scenario fields:**

| Field | Type | Required | Purpose |
|---|---|---|---|
| `id` | string | yes | Unique identifier |
| `type` | `"scenario"` | yes | Discriminator for rendering |
| `dimension` / `sub_dimension` | string | yes | What this scenario measures |
| `maslow_level` | string | yes | Which need level |
| `narrative` | string | yes | The situation description |
| `choices` | object[] | yes | Each with `key`, `text`, `quadrant_weight` |
| `choices[].quadrant_weight` | object | yes | Maps to quadrant archetypes with weights |
| `follow_up_prompt` | string | no | Optional free-text prompt after choice |
| `order` | integer | yes | Presentation order |

### Response Flow: Direct API + Agent Notification

**Likert responses bypass the agent** — routing 200 clicks through the LLM would be slow, expensive, and pointless:

```
User clicks Likert button
  -> Frontend calls POST /api/assessment/responses
    -> Backend writes to assessment_state table
    -> Backend emits assessment.progress SSE event
    -> Results Panel updates
  -> When batch complete:
    -> Frontend sends POST /api/chat with auto-message: {"type": "batch_complete", "batch_id": "..."}
    -> Agent receives notification, calls get_assessment_state(), presents next batch
```

**Scenario responses go through a hybrid path:**

```
User clicks scenario choice
  -> Frontend calls POST /api/assessment/responses (saves choice + quadrant_weight)
  -> If scenario has follow_up_prompt:
    -> Agent asks the follow-up conversationally
    -> User types free-text response
    -> Agent calls save_scenario_response() to append free-text
```

### Frontend Components

Three new rendering components (vanilla JS):

- **`LikertBatchCard`** — inline chat widget showing 2-5 questions with clickable radio scales. Each click fires immediate `POST /api/assessment/responses`. Visual confirmation checkmark on save.
- **`ScenarioCard`** — inline chat widget showing choices as clickable buttons. Single-click selection, then disabled.
- **Results Panel** — unchanged from MVP plan, receives `assessment.progress` SSE events.

### New API Endpoints

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/assessment/questions` | GET | Returns the full question bank JSON (cached) |
| `/api/assessment/questions/{dimension}` | GET | Returns questions for a specific dimension |
| `/api/assessment/responses` | POST | `{user_id, question_id, type, score?, choice_key?, quadrant_weight?}` — saves a single response |
| `/api/assessment/responses/batch` | POST | Saves multiple responses at once |
| `/api/assessment/state` | GET | Current assessment progress |

### Agent Tool Changes

| Tool | Change |
|---|---|
| `present_question_batch(question_ids)` | **NEW** — emits `assessment.question_batch` SSE event |
| `present_scenario(scenario_id)` | **NEW** — emits `assessment.scenario` SSE event |
| `get_assessment_state()` | **CHANGED** — now returns batch-level summaries (dimension averages, completion %) |
| `save_assessment_response()` | **KEPT but reduced role** — still available for edge cases |

### SSE Event Schema Changes

```json
{"event": "assessment.question_batch", "data": {"batch_id": "...", "sub_dimension": "...", "dimension": "...", "questions": [{"id": "...", "text": "...", "scale_type": "...", "scale_labels": [...]}]}}

{"event": "assessment.scenario", "data": {"scenario_id": "...", "dimension": "...", "narrative": "...", "choices": [{"key": "a", "text": "..."}], "has_follow_up": true}}

{"event": "assessment.progress", "data": {"answered": 32, "total": 200, "current_dimension": "...", "dimension_progress": {"Emotional Awareness": {"answered": 12, "total": 15, "avg_score": 3.8}}, "scenarios_completed": 3, "scenarios_total": 20}}
```

Note: `assessment.scenario` omits `quadrant_weight` from choices — that's scoring data the frontend doesn't need.

### File Structure Additions

```
api/assessment.py                  # NEW: assessment response endpoints
frontend/js/components/
  likert-card.js                   # NEW: LikertBatchCard component
  scenario-card.js                 # NEW: ScenarioCard component
agents/transmutation/question_bank.py  # NEW: loads/indexes questions.json
```

---

## 3. Time Estimate Reconciliation

**Issue**: Product concept says "20-40 minutes" but MVP plan says "70-100 conversational turns across multiple sessions." With hybrid UI (clickable Likert), estimates need updating.

**Status**: DRAFTED (resolved as part of Section 2)

**Resolution**:

With clickable Likert scales, the time breakdown becomes:

| Component | Count | Time per item | Subtotal |
|---|---|---|---|
| Likert questions (clickable) | ~200 | ~3-4 seconds/click | **10-13 min** |
| Agent grouping/context messages | ~15-20 batches | ~15 seconds each | **4-5 min** |
| Behavioral scenarios (agent-narrated) | ~20 | ~45-60 seconds each | **15-20 min** |
| Scenario free-text follow-ups | ~10 (subset) | ~60-90 seconds each | **10-15 min** |

**Total estimated assessment time: 40-55 minutes** (down from 60-90+ with all-chat delivery, ~40% reduction).

**Session distribution**: Most users will not complete this in one sitting. The agent proactively offers save points after each dimension (~every 3-5 minutes): "Good progress — we've finished Emotional Awareness. Want to continue or pick this up later?"

**Reassessment time**: Targeted reassessment (only roadmap-relevant dimensions) should take 10-15 minutes.

**Action**: Update product concept from "20-40 minutes" to "45-55 minutes across multiple sessions" and update MVP plan to reflect hybrid UX timing.

---

## 4. Tool Signatures — IDs Not Data Blobs

**Issue**: `generate_profile_snapshot(profile_data)` and `generate_roadmap(profile_data)` pass data through the LLM unnecessarily. Should take IDs and pull data internally.

**Status**: DRAFTED

**Resolution**:

### Principle

**Tools should fetch their own data internally. The LLM provides only what it uniquely knows — user intent, which action to take, and values extracted from conversation — never data blobs that already exist in the database.**

When the LLM passes data through a tool call, three things go wrong:
1. **Token waste** — profile data can be thousands of tokens
2. **Corruption risk** — the LLM may subtly alter, truncate, or hallucinate fields
3. **No added value** — the tool has DB access and can fetch the same data by ID

### Summary of Changes

| Tool | Before | After | Rationale |
|------|--------|-------|-----------|
| `generate_profile_snapshot(profile_data)` | Takes full profile data blob | `generate_profile_snapshot()` — no params | Tool fetches assessment data from DB via closure context |
| `generate_roadmap(profile_data)` | Takes full profile data blob | `generate_roadmap()` — no params | Tool fetches profile from DB via closure context |
| `save_profile_snapshot(snapshot)` | Takes full snapshot blob | `save_profile_snapshot(interpretation)` — LLM narrative only | Deterministic data already computed; LLM provides only its narrative interpretation |
| `save_roadmap(roadmap)` | Takes full roadmap blob | **Flagged for design review** — depends on whether roadmap includes LLM-generated narrative |

### Tools Where Data Pass-Through IS Correct

These tools correctly accept data from the LLM because the values **originate from the conversation**:

- `save_assessment_response(dimension, sub_dimension, question_id, score)` — LLM extracts/confirms the user's score
- `save_scenario_response(scenario_id, choice, free_text)` — choice and free_text come from the user
- `update_education_progress(topic, understanding_score)` — understanding_score is the LLM's judgment of comprehension (note: this changes in Section 5)
- `log_practice_entry(practice_id, reflection, self_rating)` — reflection is user's text, self_rating is user's number
- `advance_phase(new_phase, reason)` — new_phase is a routing decision, reason is audit context

### Full Tool Signatures by Sub-Agent

**Assessment Agent** — no changes (all params originate from conversation)

**Profile Agent:**
- `get_user_profile()` — no change
- `generate_profile_snapshot()` — **CHANGED** (removed `profile_data` param)
- `save_profile_snapshot(interpretation)` — **CHANGED** (LLM provides only narrative)
- `advance_phase(new_phase, reason)` — no change

**Education Agent** — no changes (but `update_education_progress` gets replaced per Section 5)

**Development Agent:**
- `get_user_profile()` — no change
- `get_development_roadmap()` — no change
- `generate_roadmap()` — **CHANGED** (removed `profile_data` param)
- `save_roadmap(roadmap)` — **flagged** for design review
- `log_practice_entry(practice_id, reflection, self_rating)` — no change
- `get_practice_history(practice_id)` — no change
- `advance_phase(new_phase, reason)` — no change

**Reassessment Agent:**
- `generate_comparison_snapshot(previous_snapshot_id)` — no change (already takes ID)
- `save_profile_snapshot(interpretation)` — **CHANGED** (same as profile agent)
- All others — no change

### Decision Rule for Future Tools

**"Does this parameter value come from the conversation, or from the database?"**

- **From conversation** (user's words, LLM's judgment, routing decisions) -> parameter on the tool signature
- **From database** (scores, profiles, snapshots, history) -> tool fetches internally via user ID from closure context or a minimal ID parameter

---

## 5. Comprehension Check Methodology

**Issue**: Education phase comprehension checks scored by LLM are subjective, contradicting the paper's own warning about self-report bias (Section 7.3). Need structured, deterministic scoring.

**Status**: DRAFTED

**Resolution**:

### Approach: Hybrid — Structured Scoring + Open Reflection

Structured choice questions produce the **deterministic score** that gates phase advancement. Optional open-ended reflection invites depth. The LLM provides qualitative feedback on reflections but this feedback has **zero effect on the score**.

**Why this approach:**
1. **Deterministic gate, non-deterministic depth** — reproducible, auditable, non-biased scoring
2. **Mirrors the Assessment phase** — behavioral scenarios already use structured choice for scoring + optional free-text. Consistent UX.
3. **Addresses Section 7.3** — structured comprehension questions with correct answers are behavioral measures of understanding, not self-report
4. **LLM stays in its lane** — teaches, explains, provides feedback. Never generates scores.

### JSON Schema: Comprehension Check Questions

**File:** `data/comprehension_checks.json`

```json
{
  "Emotional Awareness": {
    "what_this_means": [
      {
        "id": "cc_emo_aware_cat1_q1",
        "type": "identify_pattern",
        "stem": "Jamie notices they feel anxious every Sunday evening but has never connected it to their Monday morning team meetings. In transmutarian terms, what is happening?",
        "options": [
          {"key": "a", "text": "Jamie is a Transmuter — they are filtering the anxiety before it affects others"},
          {"key": "b", "text": "Jamie has low Emotional Awareness — the deprivation signal exists but is not yet consciously recognized"},
          {"key": "c", "text": "Jamie is an Extractor — they are drawing anxiety from the team meetings"},
          {"key": "d", "text": "Jamie has high Emotional Regulation — they are successfully managing the anxiety"}
        ],
        "correct_option": "b",
        "explanation": "Emotional Awareness is the capacity to consciously recognize what you are feeling and why. Jamie experiences the anxiety but hasn't connected it to its source.",
        "reflection_prompt": "Think about your own score on Emotional Awareness. Is there a recurring feeling in your life that you experience but haven't fully connected to its source?",
        "difficulty": "foundational"
      }
    ]
  }
}
```

**Question types**: `apply_concept`, `identify_pattern`, `predict_outcome` — all scenario-based, testing application not recall.

### Tool Signature Change

```python
# Before (LLM decides the score):
update_education_progress(topic, understanding_score)

# After (tool computes the score from structured answers):
record_comprehension_answer(dimension, category, question_id, selected_option)
# -> Looks up correct_option from comprehension_checks.json
# -> Updates questions_answered and questions_correct
# -> Recomputes understanding_score deterministically
# -> Returns {correct: bool, explanation: str, score: int}
```

### Revised education_progress Data Model

```json
{
  "dimension": {
    "category": {
      "understanding_score": 0-100,
      "questions_answered": ["cc_emo_aware_cat1_q1"],
      "questions_correct": ["cc_emo_aware_cat1_q1"],
      "last_discussed": epoch,
      "reflection_given": true
    }
  }
}
```

`understanding_score` is now computed as `(questions_correct / questions_answered) * 100` — deterministic, not LLM-generated.

### Revised advance_phase Predicate

```
advance_phase("development", reason) requires ALL of:
  1. Top 3 weakest priority dimensions (by profile score) have been covered
  2. Each of those 3 dimensions has >= 60% comprehension score
  3. All 5 education categories per dimension have at least 1 comprehension question answered
```

### Question Scope

| Item | Count |
|---|---|
| Total comprehension questions | 130 (13 dims x 5 categories x 2 questions) |
| Questions a user encounters | ~30 (top 3 weakest dims x 5 cats x 2 questions) |
| Authored in first pass | ~30-40 (most common weak dimensions + extras) |

### UI Rendering

Comprehension questions render as clickable options in the chat — same `StructuredChoice` component used for Likert and behavioral scenarios. Progress bars in the Results Panel update via `education.comprehension` SSE events.

### SSE Event

```json
{"event": "education.comprehension", "data": {"dimension": "...", "category": "...", "question_id": "...", "correct": true, "score": 67, "categories_covered": 4, "categories_total": 5}}
```

---

## 6. Exit Condition for Dev/Reassessment Loop

**Issue**: No defined graduation or winding-down mechanism. The development-reassessment loop runs indefinitely. Contradicts "regenerative, not dependent" philosophy.

**Status**: DRAFTED

**Resolution**:

### Graduation Criteria: Convergence Signal (Any 2 of 3)

| Indicator | What It Measures | Trigger Condition |
|---|---|---|
| **Pattern Stability** | Scores stopped meaningfully changing | Delta across all targeted dimensions < 5% for two consecutive reassessment cycles |
| **Quadrant Consolidation** | Transmutation pattern has settled | Same quadrant placement for two consecutive reassessments |
| **Self-Assessed Readiness** | User believes they can continue independently | User explicitly indicates readiness when prompted |

**Why "any two of three"**: A single indicator is gameable or misleading. Stable scores alone might mean early plateau. Self-assessed readiness alone might mean disengagement. Two converging signals provide confidence.

**What is explicitly NOT a graduation criterion:**
- Reaching the Transmuter quadrant (Conduit is a valid stabilization point)
- A minimum score on any dimension
- A time-based deadline

### The Graduation Experience

When criteria converge, the reassessment agent initiates a structured closing sequence:

1. **Longitudinal Review** — first snapshot vs. current, all intermediate reassessments, dimension-by-dimension movement
2. **Pattern Narrative** — LLM synthesizes the user's journey into a personalized narrative
3. **Independent Practice Map** — reference document: which practices worked, which dimensions to watch, specific vulnerabilities
4. **Graduation Snapshot** — final profile snapshot marked `assessment_type: 'graduation'`
5. **Check-In Invitation** — explicit framing that the door remains open

### Post-Graduation: Periodic Check-Ins

| Aspect | Active Loop | Check-In |
|---|---|---|
| Assessment scope | Targeted (roadmap dimensions) | Full (all dimensions) |
| Output | New roadmap + continued cycle | Comparison snapshot only |
| Phase | `reassessment -> development` | `check_in -> graduated` |
| Agent behavior | Prescriptive | Reflective |
| Re-entry | N/A | Offered if significant regression detected |

Check-in cadence: Agent suggests 3 months, then 6 months, then annually. Suggestions only — no push notifications.

### Phase Model Changes

```
orientation -> assessment -> profile -> education -> development -> reassessment
                                                         ^              |
                                                         +--------------+  (active loop)
                                                                        |
                                                                   graduation (closing sequence)
                                                                        |
                                                                   graduated (terminal state)
                                                                        |
                                                                    check_in (periodic return)
                                                                        |
                                                                   graduated (default return)
```

`current_phase` valid values: `orientation | assessment | profile | education | development | reassessment | graduation | graduated | check_in`

### Data Model Changes

```sql
-- Extend users table
users.current_phase   -- add: 'orientation', 'graduation', 'graduated', 'check_in'
users.graduated_at    TIMESTAMP  -- NULL until graduation

-- Extend assessment_state
assessment_state.assessment_type  -- add: 'check_in'

-- New: graduation_record
graduation_record
  id                    TEXT PRIMARY KEY
  user_id               TEXT FK
  final_snapshot_id     TEXT FK
  initial_snapshot_id   TEXT FK
  practice_map          JSON     -- independent practice reference
  pattern_narrative     TEXT     -- LLM-generated journey narrative
  graduation_indicators JSON     -- which 2 of 3 criteria were met
  created_at            TIMESTAMP

-- New: check_in_log
check_in_log
  id                    TEXT PRIMARY KEY
  user_id               TEXT FK
  snapshot_id           TEXT FK
  graduation_snapshot_id TEXT FK
  regression_detected   BOOLEAN
  re_entered_development BOOLEAN DEFAULT FALSE
  created_at            TIMESTAMP
```

### Agent Architecture Changes

```
transmutation_root_agent (orchestrator)
  +-- assessment_agent
  +-- profile_agent
  +-- education_agent
  +-- development_agent
  +-- reassessment_agent      -- gains graduation-readiness evaluation
  +-- graduation_agent        -- NEW: closing sequence + artifact generation
  +-- check_in_agent          -- NEW: post-graduation periodic assessment
```

### Avoiding Perverse Incentives

| Risk | Mitigation |
|---|---|
| Gaming scores to graduate faster | Graduation requires *stable* scores, not high scores. Inflating just means they stabilize at inflated levels. |
| Feeling pressured to graduate | Agent never initiates graduation unprompted by indicators. No countdown or progress bar. |
| Treating Transmuter as "correct answer" | Graduation criteria are quadrant-agnostic. Stability and readiness, not placement. |
| Rushing through development | Pattern stability requires two consecutive reassessment cycles with low deltas. |
| Anxiety about losing progress post-graduation | Check-in system exists. Independent practice map gives something concrete to keep. |
| Re-entry feeling like failure | Framed as maintenance: "Patterns shift with life changes. Returning is exactly what this is for." |

---

## 7. Cross-Cutting Concerns

### 7a. Error Handling / Off-Topic in Chat

**Issue**: No defined behavior for off-topic input, user distress, or disagreement with assessment.

**Status**: DRAFTED

**Resolution**:

**Off-topic messages**: The `boundary.py` prompt handles this. Agent acknowledges briefly and redirects: "That's an interesting thought — let's come back to it after we finish this section." If the user persists (3+ consecutive off-topic messages): "It seems like you'd rather talk about something else. We can pick up anytime — just say 'let's continue' when you're ready." Session stays open; no phase change.

**User argues with assessment**: The `no_shame.py` prompt instructs the agent to never defend scores. Validate their perspective ("You know yourself best"), explain what the data reflects without insisting, and offer to re-answer specific questions. `save_assessment_response` overwrites prior responses for the same `question_id`, so re-answering is mechanically supported.

**Inappropriate content — three-tier response via `safety.py`**:
1. **Mild** (profanity, frustration): Acknowledge the emotion, continue normally.
2. **Moderate** (hostile, dismissive): De-escalate and offer to pause.
3. **Severe** (self-harm indicators, crisis language): Immediately provide crisis resources (988 Lifeline, Crisis Text Line), stop asking assessment questions, set `safety_flag` in session state. Agent does NOT attempt to counsel.

**Implementation**: All via prompt text, not middleware. A `flag_safety_concern(reason)` tool logs to a `safety_log` table (user_id, timestamp, reason). Available to all sub-agents.

### 7b. Agent Handoff UX

**Issue**: Phase transitions have no defined user-facing experience.

**Status**: DRAFTED

**Resolution**:

Handoffs are conversational, not mechanical:

1. Current sub-agent wraps up with a summary: "Great — we've completed the assessment. Let me hand you over to the next phase."
2. A system message appears in chat (gray italic): "Phase transition: Assessment -> Profile"
3. Results Panel updates via SSE: `{"event": "phase.transition", "data": {"from": "assessment", "to": "profile"}}`
4. New sub-agent introduces itself with context: "Hi Kevin — I've computed your profile. Let's walk through what it shows..."

**Visual indicators:**
- Results Panel header always shows current phase with a colored dot
- Bottom session bar shows history; clicking a past session loads conversation but does NOT change phase

### 7c. Data Export

**Issue**: No export capability. Contradicts regenerative philosophy.

**Status**: DRAFTED

**Resolution**:

**Endpoint**: `GET /export/{user_id}` — JSON file download. Protected by session cookie (own data only).

**Trigger**: "Download My Data" button in Results Panel footer, visible in all phases.

**Payload**: All user data — assessments, profile snapshots, education progress, roadmaps, practice journal. Excludes: password hashes, raw ADK session state, spider chart PNGs (data to regenerate is in snapshot), safety log.

**Implementation**: One query per table, assembled into dict, `json.dumps(indent=2)`. No pagination — user data is < 1MB.

### 7d. Chat API Contract

**Issue**: Request/response format not specified. SSE streaming unclear.

**Status**: DRAFTED

**Resolution**:

**Session creation:**
```
POST /sessions
Request:  {} (user_id from session cookie)
Response: {"session_id": "uuid", "created_at": "..."}
```

**Chat — POST returns SSE stream** (same pattern as WCRP's `run_agent_with_sse()`):
```
POST /chat/{session_id}
Content-Type: application/json
Request:  {"message": "I tend to notice when I'm frustrated but..."}
Response: text/event-stream (SSE)
```

**SSE event sequence:**
```
event: agent.thinking        data: {}
event: agent.message.chunk   data: {"text": "That's a really "}
event: agent.message.chunk   data: {"text": "important insight. "}
event: tool.call             data: {"tool": "save_assessment_response", "args": {...}}
event: tool.result           data: {"tool": "save_assessment_response", "result": "saved"}
event: assessment.progress   data: {"answered": 33, ...}
event: agent.message.chunk   data: {"text": "\n\nNow, thinking about..."}
event: agent.message.complete data: {"full_text": "..."}
```

Domain events (`assessment.progress`, `profile.snapshot`, etc.) are emitted by tool functions during the same stream. `phase.transition` fires when `advance_phase` is called.

**Error**: `event: error data: {"code": "model_error", "message": "..."}` — stream closes, frontend shows error and re-enables input.

**Session list**: `GET /sessions` returns all user sessions with metadata.

### 7e. Rate Limiting

**Issue**: No abuse protection.

**Status**: DRAFTED

**Resolution**:

**Library**: `slowapi` (wraps `limits`, integrates with FastAPI). Single dependency.

| Endpoint | Limit | Key |
|---|---|---|
| `POST /auth/register` | 5/hour | IP address |
| `POST /auth/login` | 10/minute | IP address |
| `POST /chat/{session_id}` | 30/minute | User ID |
| `GET /export/{user_id}` | 5/hour | User ID |

**Storage**: In-memory. No Redis needed for MVP. Limits reset on restart.

### 7f. Model Cost Awareness

**Issue**: No cost estimates per model.

**Status**: DRAFTED

**Resolution**:

**UI**: "Session Cost" widget in Results Panel footer: `Est. cost this session: $0.12 | Total: $2.45`

**Calculation**: Track input/output tokens per agent turn from LiteLLM's `response.usage`. Multiply by per-token cost from static lookup in `config.yaml`:

```yaml
model_costs:  # per 1M tokens
  claude-sonnet-4-5-20250514: { input: 3.00, output: 15.00 }
  gpt-4o: { input: 2.50, output: 10.00 }
  gpt-4o-mini: { input: 0.15, output: 0.60 }
  ollama/*: { input: 0.00, output: 0.00 }
```

**Rough estimates for full assessment cycle:**

| Model | Est. Cost |
|---|---|
| Claude Sonnet 4.5 | ~$4.20 |
| GPT-4o | ~$3.00 |
| GPT-4o-mini | ~$0.18 |
| Ollama (local) | $0.00 |

**Storage**: Running totals in `adk_sessions` table: `total_input_tokens`, `total_output_tokens`, `estimated_cost_usd`. SSE event: `session.cost`.

---

## 8. Architectural Concerns

### 8a. Phase Model — Read-Only Access to Past Phases

**Issue**: `current_phase` is strictly linear. User can't view past phase data.

**Status**: DRAFTED

**Resolution**:

Separate `current_phase` (agent's active working phase, controls tool write access) from what the Results Panel displays.

- Results Panel renders **tabs based on data existence**, not `current_phase`:
  - Assessment tab -> visible if `assessment_state` exists
  - Profile tab -> visible if `profile_snapshots` exists
  - Education tab -> visible if `education_progress` exists
  - Development tab -> visible if `development_roadmap` exists
  - Reassessment tab -> visible if `profile_snapshots` with `previous_snapshot_id != NULL` exists
- All `get_*` read tools have **no phase gate**. Only `save_*` and `advance_phase()` validate `current_phase`.
- Add `GET /api/results/{user_id}` returning all completed phase data. Frontend renders tabs for each phase with data. Active phase tab visually distinguished.
- No "go back to phase" for the agent — it always operates in `current_phase`. The Results Panel is a read-only viewer.

### 8b. Session State Size

**Issue**: Session state grows large during long assessment sessions.

**Status**: DRAFTED

**Resolution**:

- **What's stored**: ADK conversation message history (user/agent turns) + ephemeral state variables (`last_dimension`, `questions_this_session`).
- **Expected size**: 100-300 KB per session for 70-100 turns. Manageable per-session.
- **Key design**: Domain state lives in domain tables, not session state. New sessions bootstrap from `get_assessment_state()` / `get_user_profile()`, never from prior conversation history.
- **Archival**: When a new session is created, mark prior sessions as `archived = true`. Keep rows for audit but don't serve to the agent.
- **Schema addition**: `adk_sessions.archived BOOLEAN DEFAULT FALSE`. Session list endpoint returns only non-archived for active use.
- **No mid-session truncation**: Let conversation grow naturally within a sitting.

### 8c. DB Migration Strategy

**Issue**: No plan for schema changes during development.

**Status**: DRAFTED

**Resolution**:

**Numbered SQL files with a version table.** Light enough for MVP, disciplined enough to prevent headaches.

- `schema_version` table: `CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)`
- Migration files in `db/migrations/`: `001_initial.sql`, `002_add_archived_column.sql`, etc.
- On startup, `database.py` checks highest applied version, runs unapplied migrations in order. ~30 lines of Python.
- Also provide `db/reset.sh` that deletes the SQLite file and reruns for clean start during dev.
- Replaced by Alembic when porting to WCRP (already in porting checklist item 7).

### 8d. Assessment Completion Threshold

**Issue**: >=80% threshold allows skipping 20% of questions.

**Status**: DRAFTED

**Resolution**:

Reframe from "threshold" to **"applicability-aware completion"**:

- Add `not_applicable` response option alongside the Likert scale. When a question doesn't apply to the user's context (parenting for non-parents, workplace for retirees), `save_assessment_response()` records `score: null, skipped_reason: "not_applicable"`. These don't count toward answered *or* total.
- **Completion predicate**: All *applicable* questions must be answered.
- **Safety floor**: If more than 20% of questions within any single dimension are marked N/A, flag that dimension as "insufficient data" in the profile.
- **Minimum per dimension**: At least 60% of questions per dimension must be answered for that dimension's score to be computed. Below that, dimension is excluded from quadrant calculation until reassessment fills gaps.
- Agent does not offer "skip" as a general option. It presents questions conversationally and only marks N/A when the user's life context genuinely doesn't match.

### 8e. Roadmap Adjustment Mid-Cycle

**Issue**: No mechanism to adjust roadmap before full reassessment.

**Status**: DRAFTED

**Resolution**:

Add `update_roadmap()` to the development agent:

- **Tool**: `update_roadmap(adjustment_reason, retain_practices: list[str], drop_practices: list[str])` — creates a new `development_roadmap` row with `parent_roadmap_id` linking to the original.
- **Trigger conditions** (prompt guidance, not hard gates):
  1. User-reported difficulty: 3+ practice entries on the same practice where `self_rating` trends downward or stays flat
  2. Explicit user request
  3. Life change making current practices irrelevant
- **Constraints**:
  - **Cooldown**: No more than one adjustment per 7 days (enforced in tool by checking `development_roadmap.created_at`)
  - **Scope limit**: Can swap individual practices but cannot change targeted dimensions (that requires full reassessment)
  - Agent must explain what's changing and why
- **Schema addition**: `development_roadmap.parent_roadmap_id TEXT FK`

### 8f. Reassessment Staleness

**Issue**: Non-targeted dimensions never get re-measured.

**Status**: DRAFTED

**Resolution**:

Introduce **sentinel check-ins** during reassessment:

- After completing targeted dimension reassessment, the agent asks **5 sentinel questions** from the 2-3 most stale non-targeted dimensions.
- **Staleness**: Days since dimension was last assessed. Pick 2-3 with highest staleness.
- **Question selection**: For each stale dimension, pick 1-2 questions that had the most extreme scores. Changes in extremes are the strongest signal.
- **Scoring**: Weighted blend: 70% prior assessment + 30% sentinel extrapolation. Prevents single-question score swings while detecting drift.
- **Full reassessment trigger**: If sentinel detects > 15 point shift in any non-targeted dimension, flag it for full reassessment next cycle.
- **Cadence**: Every reassessment cycle. No dimension should go more than 2 cycles without at least sentinel coverage. Force-include at 3 cycles.
- **Impact**: Adds ~5-8 minutes per reassessment. Acceptable for data integrity.
