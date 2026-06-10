// @ts-check
/**
 * Harness journey spec (TEST-003): full-stack E2E test against a live server
 * running in mock mode with a pre-seeded development-phase user.
 *
 * Prerequisites (handled by `make test-harness`):
 *   - Server started with TRANSMUTE_MOCK_SCENARIO pointing to
 *     tests/harness/scenarios/education_session.json
 *   - User seeded to development phase: 10 practice entries, roadmap ≥ 35 days
 *   - HARNESS_EMAIL env var set to the seeded user's email
 *
 * IMPORTANT: This spec deliberately does NOT use route mocks. It calls the
 * real server and the real API endpoints. The server itself is the mock —
 * MockLlm replays the scenario instead of calling an LLM provider.
 *
 * Security (secure-defaults pattern): the fail-fast guard at the top of each
 * test FAILS unconditionally if the server is not in mock mode. It does NOT
 * skip — a skipped test could silently pass while triggering paid LLM calls.
 */

const { test, expect, request } = require('@playwright/test');
const path = require('path');
const fs = require('fs');

const SCREENSHOTS_DIR = path.join(__dirname, 'screenshots');
const BASE_URL = process.env.BASE_URL || 'http://localhost:54718';
const HARNESS_EMAIL = process.env.HARNESS_EMAIL || 'harness@example.com';
const HARNESS_PASSWORD = 'Seed1234!';

// Scripted agent reply from education_session.json (step 1 of transmutation_engine)
const EXPECTED_AGENT_REPLY = 'Welcome back! You\'re in the Development phase.';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Fail-fast guard: assert the live server is in mock mode.
 *
 * R5 (testing-e2e-patterns): assertions must be unconditional — this FAILS
 * the test with an actionable message rather than skipping it. A skip would
 * allow accidental real-LLM execution to pass silently.
 */
async function assertMockMode(request) {
    const res = await request.get(`${BASE_URL}/health`);
    expect(res.status(), 'GET /health must return 200').toBe(200);
    const body = await res.json();
    expect(
        body.mock_mode,
        [
            'Server is NOT in mock mode — harness aborted.',
            'Set TRANSMUTE_MOCK_SCENARIO to a valid scenario path and restart the server.',
            `Got /health response: ${JSON.stringify(body)}`,
        ].join('\n'),
    ).toBe(true);
}

/**
 * Log in as the pre-seeded harness user via the real /auth/login API.
 * Returns after the main app (#app) is visible.
 */
async function loginAndWait(page) {
    await page.goto('/');

    // Wait for auth overlay — the login form is rendered inside #auth-container
    await page.locator('#auth-container').waitFor({ state: 'visible', timeout: 10_000 });
    // Baseline landmark: "Sign In" heading inside the form
    await expect(page.getByRole('heading', { name: 'Sign In' })).toBeVisible({ timeout: 5_000 });

    // Fill in credentials using semantic placeholders (matches auth.js _createInput calls)
    await page.getByPlaceholder('Email').fill(HARNESS_EMAIL);
    await page.getByPlaceholder('Password').fill(HARNESS_PASSWORD);
    await page.getByRole('button', { name: 'Sign In' }).click();

    // Auth overlay should hide; #app should become visible
    await expect(page.locator('#app')).toBeVisible({ timeout: 15_000 });
    // Wait for initial data fetch (sessions + results) to complete
    await page.waitForLoadState('networkidle');
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test.describe('Harness journey — development-phase user (TEST-003)', () => {

    test.beforeEach(async ({ page }) => {
        // R6 (testing-e2e-patterns): register page error listener
        page._jsErrors = [];
        page.on('pageerror', err => page._jsErrors.push(err.message));
    });

    test.afterEach(async ({ page }) => {
        const errs = page._jsErrors || [];
        if (errs.length > 0) throw new Error('Uncaught JS errors during harness journey: ' + errs.join('; '));
    });

    // ── journey-01: mock mode guard ──────────────────────────────────────────

    test('journey-01: /api/health reports mock_mode=true (fail-fast guard)', async ({ request }) => {
        await assertMockMode(request);
    });

    // ── journey-02: login as seeded user ─────────────────────────────────────

    test('journey-02: login as seeded development-phase user', async ({ page, request }) => {
        await assertMockMode(request);
        await loginAndWait(page);

        if (!fs.existsSync(SCREENSHOTS_DIR)) fs.mkdirSync(SCREENSHOTS_DIR, { recursive: true });
        await page.screenshot({ path: path.join(SCREENSHOTS_DIR, 'harness-01-logged-in.png') });

        // R7 (testing-e2e-patterns): verify a meaningful landmark before drilling into details
        await expect(page.locator('#app')).toBeVisible();
        // Verify authenticated user name is shown in the top bar
        // (seeder sets name from the email local part: "harness" for "harness@example.com")
        await expect(page.locator('#user-name')).not.toBeEmpty();
    });

    // ── journey-03: Development tab shows roadmap and practice counter ────────

    test('journey-03: Development tab renders roadmap and practice counter', async ({ page, request }) => {
        await assertMockMode(request);
        await loginAndWait(page);

        // The Development tab is visible when _resultsData.development_roadmap exists.
        // Results.update() is called by App.initMainApp after the /api/results fetch.
        // Use role=tab (results.js sets role="tab" on each button).
        const devTab = page.getByRole('tab', { name: /Development/i });
        await expect(devTab).toBeVisible({ timeout: 10_000 });
        await devTab.click();

        // R7: verify baseline before detail assertions
        const resultsPanel = page.locator('#results-panel');
        await expect(resultsPanel).toBeVisible();

        // Practice counter — seeded with 10 entries
        await expect(page.getByText(/Practice entries: 10 \/ 10/)).toBeVisible({ timeout: 5_000 });

        // Roadmap section header
        await expect(page.getByText('Current Roadmap')).toBeVisible({ timeout: 5_000 });

        if (!fs.existsSync(SCREENSHOTS_DIR)) fs.mkdirSync(SCREENSHOTS_DIR, { recursive: true });
        await page.screenshot({ path: path.join(SCREENSHOTS_DIR, 'harness-02-development-tab.png') });
    });

    // ── journey-04: chat with mock agent, verify scripted reply ──────────────

    test('journey-04: send a message and receive the scripted mock agent reply', async ({ page, request }) => {
        await assertMockMode(request);
        await loginAndWait(page);

        // Ensure a session exists (App.initMainApp creates one if none exist)
        const chatInput = page.locator('#chat-input');
        await expect(chatInput).toBeVisible({ timeout: 10_000 });

        // Type and send the message
        await chatInput.fill('Hello, how are things going?');
        await page.locator('.chat-send-btn').click();

        // User message bubble should appear immediately
        await expect(page.locator('.chat-msg--user').first()).toBeVisible({ timeout: 5_000 });

        // Wait for the scripted agent reply from education_session.json.
        // The mock emits agent.message.complete which finalizes the agent bubble.
        await expect(page.locator('.chat-msg--agent').first()).toBeVisible({ timeout: 15_000 });
        await expect(page.locator('.chat-messages')).toContainText(EXPECTED_AGENT_REPLY, { timeout: 15_000 });

        if (!fs.existsSync(SCREENSHOTS_DIR)) fs.mkdirSync(SCREENSHOTS_DIR, { recursive: true });
        await page.screenshot({ path: path.join(SCREENSHOTS_DIR, 'harness-03-agent-reply.png') });
    });

    // ── journey-05: SSE cost is $0.00 in mock mode ────────────────────────────

    test('journey-05: cost display shows $0.00 after mock chat turn', async ({ page, request }) => {
        await assertMockMode(request);
        await loginAndWait(page);

        const chatInput = page.locator('#chat-input');
        await expect(chatInput).toBeVisible({ timeout: 10_000 });
        await chatInput.fill('Hello!');
        await page.locator('.chat-send-btn').click();

        // Wait for agent reply to confirm the SSE turn completed
        await expect(page.locator('.chat-msg--agent').first()).toBeVisible({ timeout: 15_000 });
        await expect(page.locator('.chat-messages')).toContainText(EXPECTED_AGENT_REPLY, { timeout: 15_000 });

        // The session.cost SSE event sets the cost display to $0.00 in mock mode.
        // The cost display button (#cost-display) is updated by Chat._updateCostDisplay.
        const costDisplay = page.locator('#cost-display');
        await expect(costDisplay).toBeVisible({ timeout: 5_000 });
        await expect(costDisplay).toContainText('$0.00', { timeout: 5_000 });

        if (!fs.existsSync(SCREENSHOTS_DIR)) fs.mkdirSync(SCREENSHOTS_DIR, { recursive: true });
        await page.screenshot({ path: path.join(SCREENSHOTS_DIR, 'harness-04-zero-cost.png') });
    });

});
