.PHONY: docker-up docker-down docker-build docker-logs \
        seed mock-run test-harness

# ── Docker targets ────────────────────────────────────────────────────────────

docker-up:
	docker compose down && docker compose up -d --build
	@echo ""
	@echo "Web UI available at: http://localhost:54718"

docker-down:
	docker compose down

docker-build:
	docker compose build

docker-logs:
	docker compose logs -f backend

# ── Test-harness targets ──────────────────────────────────────────────────────
#
# These targets orchestrate the cost-free full-stack test harness:
#   seed        — fast-forward a user to a specific phase with production-shaped data
#   mock-run    — start the FastAPI server in scripted-mock mode (no real LLM calls)
#   test-harness — full end-to-end harness: seed → mock server → Playwright → teardown

# seed: Run the phase seeder CLI.
#
# Required environment variables:
#   PHASE   — target phase (e.g. assessment, development, graduated)
#   EMAIL   — user email address
#
# Optional environment variables (defaults mirror seeder defaults):
#   PASSWORD  — user password            (default: Seed1234!)
#   ARCHETYPE — target archetype         (default: transmuter)
#   DAYS_AGO  — days to backdate data    (default: 35)
#   ENTRIES   — practice journal entries (default: 10)
#   DB        — explicit DB file path    (default: uses DB_PATH env var or config)
#   FORCE     — set to --force to overwrite an existing user
#
# Examples:
#   make seed PHASE=development EMAIL=dev@example.com
#   make seed PHASE=graduated   EMAIL=grad@example.com ARCHETYPE=absorber
#   make seed PHASE=assessment  EMAIL=dup@example.com  FORCE=--force
seed:
	@if [ -z "$(PHASE)" ]; then \
	  echo "error: PHASE is required. Usage: make seed PHASE=<phase> EMAIL=<email>"; \
	  exit 1; \
	fi
	@if [ -z "$(EMAIL)" ]; then \
	  echo "error: EMAIL is required. Usage: make seed PHASE=<phase> EMAIL=<email>"; \
	  exit 1; \
	fi
	python3 -m scripts.seed_phase \
	  --phase   "$(PHASE)" \
	  --email   "$(EMAIL)" \
	  $(if $(PASSWORD),--password "$(PASSWORD)") \
	  $(if $(ARCHETYPE),--archetype "$(ARCHETYPE)") \
	  $(if $(DAYS_AGO),--days-ago "$(DAYS_AGO)") \
	  $(if $(ENTRIES),--entries "$(ENTRIES)") \
	  $(if $(DB),--db "$(DB)") \
	  $(FORCE)

# mock-run: Start the FastAPI server in scripted-mock mode.
#
# Required environment variables:
#   TRANSMUTE_MOCK_SCENARIO — path to the scenario JSON file
#
# Optional environment variables:
#   DB_PATH — SQLite database path (default: transmute.db)
#
# The server binds to http://localhost:54718 (matching playwright.config.js).
# Mock mode is opt-in: absent TRANSMUTE_MOCK_SCENARIO uses the real LLM provider
# (secure-defaults pattern — the env var is the explicit opt-in signal).
#
# Example:
#   make mock-run TRANSMUTE_MOCK_SCENARIO=tests/harness/scenarios/education_session.json
mock-run:
	@if [ -z "$(TRANSMUTE_MOCK_SCENARIO)" ]; then \
	  echo "error: TRANSMUTE_MOCK_SCENARIO is required."; \
	  echo "       Example: make mock-run TRANSMUTE_MOCK_SCENARIO=tests/harness/scenarios/education_session.json"; \
	  exit 1; \
	fi
	TRANSMUTE_MOCK_SCENARIO="$(TRANSMUTE_MOCK_SCENARIO)" python3 main.py

# test-harness: Full end-to-end harness run.
#
# Orchestrates:
#   1. Seed a development-phase user (--force ensures idempotency)
#   2. Start the mock server in the background
#   3. Wait for the server to become healthy (GET /api/health)
#   4. Run the harness Playwright spec
#   5. Tear down the mock server regardless of test outcome
#
# Required environment variables:
#   TRANSMUTE_MOCK_SCENARIO — path to the scenario JSON file
#
# Optional environment variables:
#   HARNESS_EMAIL — seeded user email  (default: harness@example.com)
#   HARNESS_DB    — DB file for the run (default: /tmp/harness-run.db)
#   BASE_URL      — Playwright base URL (default: http://localhost:54718)
#
# Example:
#   make test-harness TRANSMUTE_MOCK_SCENARIO=tests/harness/scenarios/education_session.json
HARNESS_EMAIL ?= harness@example.com
HARNESS_DB    ?= /tmp/harness-run.db
BASE_URL      ?= http://localhost:54718

test-harness:
	@if [ -z "$(TRANSMUTE_MOCK_SCENARIO)" ]; then \
	  echo "error: TRANSMUTE_MOCK_SCENARIO is required."; \
	  echo "       Example: make test-harness TRANSMUTE_MOCK_SCENARIO=tests/harness/scenarios/education_session.json"; \
	  exit 1; \
	fi
	@bash -c '\
	  set -e; \
	  SERVER_PID=""; \
	  cleanup() { \
	    if [ -n "$$SERVER_PID" ]; then \
	      echo "--- Teardown: stopping mock server (pid=$$SERVER_PID) ---"; \
	      kill "$$SERVER_PID" 2>/dev/null || true; \
	    fi; \
	  }; \
	  trap cleanup EXIT; \
	  echo "--- [1/4] Seeding development-phase user: $(HARNESS_EMAIL) ---"; \
	  DB_PATH="$(HARNESS_DB)" python3 -m scripts.seed_phase \
	    --phase development \
	    --email "$(HARNESS_EMAIL)" \
	    --entries 10 \
	    --days-ago 35 \
	    --force; \
	  echo "--- [2/4] Starting mock server (background) ---"; \
	  DB_PATH="$(HARNESS_DB)" \
	  TRANSMUTE_MOCK_SCENARIO="$(TRANSMUTE_MOCK_SCENARIO)" \
	    python3 main.py & \
	  SERVER_PID=$$!; \
	  echo "--- [3/4] Waiting for server to be healthy (pid=$$SERVER_PID) ---"; \
	  for i in $$(seq 1 30); do \
	    if curl -sf "$(BASE_URL)/health" > /dev/null 2>&1; then \
	      echo "Server healthy after $${i}s"; break; \
	    fi; \
	    if [ "$$i" -eq 30 ]; then \
	      echo "error: server did not become healthy within 30s"; exit 1; \
	    fi; \
	    sleep 1; \
	  done; \
	  echo "--- [4/4] Running Playwright harness spec ---"; \
	  cd tests/e2e && \
	  BASE_URL="$(BASE_URL)" \
	  HARNESS_EMAIL="$(HARNESS_EMAIL)" \
	    npx playwright test harness-journey.spec.js; \
	  echo "--- test-harness: PASSED ---"; \
	'
