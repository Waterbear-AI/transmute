# Harness Scenario Scripts

Scenario files drive the scripted `MockLlm` (`agents/transmutation/mock_llm.py`)
so the full stack — real tools, real DB, real SSE events, real frontend —
runs without any LLM API calls.

## Running a scenario

```bash
# 1. Seed a user at the phase the scenario targets
make seed PHASE=development EMAIL=dev@test.com PASSWORD=test1234

# 2. Start the server in mock mode (explicit opt-in, loud banner)
make mock-run TRANSMUTE_MOCK_SCENARIO=tests/harness/scenarios/development_session.json

# 3. Log in at http://localhost:54718 as the seeded user and chat
```

The seeder prints the `user_id` it created — you'll paste it into chat
(see "Driving tool calls" below).

## Step semantics — read this before authoring

**One step is consumed per MODEL INVOCATION, not per user message.**
ADK re-invokes the model after every tool result, so a turn that runs a
tool and then narrates costs TWO steps:

```
user message → model pops {"call": ...} → tool runs → model pops {"say": ...}
```

Step kinds:

| Step | Effect |
|------|--------|
| `{"say": "<text>"}` | Emits assistant text; ends the model's turn |
| `{"call": "<tool>", "args": {...}, "args_from": {...}}` | Emits a function call; ADK executes the REAL tool |
| `{"transfer": "<agent_name>"}` | Emits `transfer_to_agent`; routing moves to that sub-agent |

Top-level keys: `default_say` (required — served when a queue is empty;
the script never errors mid-conversation) plus one array per agent name.
Agent names are resolved from the request's registered tools
(`_AGENT_TOOL_MARKERS` in mock_llm.py): `transmutation_engine` (root),
`assessment_agent`, `profile_agent`, `education_agent`,
`development_agent`, `reassessment_agent`, `graduation_agent`,
`check_in_agent`.

**Queues are process-global.** Steps advance across all sessions and do
not rewind — restart the server to reset a scenario.

## Driving tool calls — the `user_id` pattern

Tools take `user_id` explicitly (the real LLM reads it from its
instructions, but the mock cannot). Scenarios extract runtime values from
two sources via `args_from`:

- `tool_response.<path>` — most recent function_response in the request
  (e.g. `tool_response.question_ids[*]`)
- `user_message.<path>` — most recent user chat message parsed as JSON

So any scenario whose steps call tools expects YOU (or the Playwright
spec) to send a JSON chat message carrying the values, e.g.:

```json
{"user_id": "84bdeccb-57b6-40b4-8c9d-ca002fa4e400"}
```

Static `args` and extracted `args_from` merge; static wins on conflict.

**Known limitation:** `tool_response` reaches only the MOST RECENT tool
response. A step cannot reference a value returned two tool calls ago
(e.g. `generate_comparison_snapshot(graduation_snapshot_id)` after the
snapshot chain) — leave those tools to real-LLM sessions.

## Shipped scenarios

| File | Seed phase | What it exercises | Messages to send |
|------|-----------|-------------------|------------------|
| `education_session.json` | `development` | Login + root-agent chat round-trip, SSE plumbing (no tool calls) | any text |
| `development_session.json` | `development` | `get_development_roadmap`, `log_practice_entry` (entry counter + SSE `development.practice`) | 1: `{"user_id": "<id>"}` · 2: `{"user_id": "<id>", "reflection": "...", "self_rating": 7}` |
| `check_in_session.json` | `check_in` | Full check-in scoring chain: `get_graduation_record` → `generate_check_in_snapshot` → `save_profile_snapshot` → `detect_check_in_regression` (regression panel data) | 1: `{"user_id": "<id>"}` |

`development_session.json` logs against `seed-practice-1`, which the
seeder always creates; linkage fields are backfilled from the roadmap.
