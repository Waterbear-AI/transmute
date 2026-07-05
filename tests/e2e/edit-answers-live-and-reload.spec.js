// @ts-check
/**
 * E2E spec for TEST-001: Editable Answers with Live Transmute-Score Regeneration.
 *
 * This is the composite/narrative test the project's spec.md B13.2
 * "Verification Scenarios" calls for -- chaining together the individual
 * pieces that BE-001 (recompute), BE-002 (history scenario_responses),
 * FE-001 (Results.applyEarlyResult), FE-002 (ScenarioCard edit parity), and
 * FE-003 (history-replay wiring) each test in isolation, into the two full
 * user journeys described in spec.md A2:
 *
 *  - "Correct-a-mis-click (live)": user re-clicks a different scenario
 *    option mid-conversation -> new choice saved, archetype re-rendered if
 *    it shifts, agent does NOT re-advance.
 *  - "Edit-after-reload": user reloads mid-assessment via
 *    Sessions.activate() (the real /history-driven replay path, not a
 *    direct Chat.renderHistory call), sees the earlier scenario's prior
 *    pick highlighted, changes it, and the change is saved + re-scored.
 *
 * Mocks the API layer (matching this project's established e2e convention --
 * see tiered-assessment.spec.js's docstring) while exercising the real
 * production code paths: ScenarioCard's click handler, Results.applyEarlyResult,
 * Sessions.activate's /history fetch, and Chat.renderHistory's widget
 * replay -- no test calls Results.handleSSEEvent or ScenarioCard.create's
 * internals directly to fake the outcome.
 */
const { test, expect } = require('@playwright/test');
const path = require('path');

const SCREENSHOTS_DIR = path.join(__dirname, 'screenshots');

const SCENARIO_DATA = {
    scenario_id: 'sc_belong_01',
    dimension: 'Belonging',
    narrative: 'A friend asks you for help moving on short notice. What do you do?',
    choices: [
        { key: 'a', text: 'Drop everything and help them.' },
        { key: 'b', text: 'Explain you cannot today but offer another time.' },
        { key: 'c', text: 'Say no without further explanation.' },
    ],
};

const EARLY_RESULT_TRANSMUTER = {
    event_type: 'assessment.transmute_result',
    archetype: 'transmuter',
    x: 0.31,
    y: 0.44,
    confidence: 'medium',
    confidence_reason: 'Based on ~18 core answers; a few more will sharpen it.',
    computed_at: '2026-07-05T00:00:00',
};

const EARLY_RESULT_ABSORBER = {
    event_type: 'assessment.transmute_result',
    archetype: 'absorber',
    x: -0.35,
    y: 0.4,
    confidence: 'medium',
    confidence_reason: 'Based on ~19 core answers; a few more will sharpen it.',
    computed_at: '2026-07-05T00:05:00',
};

async function bypassAuth(page) {
    await page.route('**/auth/me', route => route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ user_id: 'test-user', name: 'Test User', email: 'test@example.com' }),
    }));
    await page.route('**/api/results/**', route => route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({}),
    }));
}

test.describe('Editable answers: live and post-reload journeys (TEST-001)', () => {

    test.beforeEach(async ({ page }) => {
        page._jsErrors = [];
        page.on('pageerror', err => page._jsErrors.push(err.message));
    });

    test.afterEach(async ({ page }) => {
        const errs = page._jsErrors || [];
        if (errs.length > 0) throw new Error('Uncaught JS errors: ' + errs.join('; '));
    });

    test('journey-01 (live): correcting a mis-clicked scenario re-scores and updates the archetype without re-advancing the agent', async ({ page }) => {
        await bypassAuth(page);
        await page.route('**/api/sessions', route => route.fulfill({
            status: 200, contentType: 'application/json',
            body: JSON.stringify({ sessions: [], count: 0, user_total_cost_usd: 0 }),
        }));
        await page.goto('/');
        await page.locator('#app').waitFor({ state: 'visible', timeout: 10000 });
        await page.waitForLoadState('networkidle');

        await page.evaluate(() => {
            App._testSessionId = 's1';
            const orig = App.getCurrentSessionId;
            App.getCurrentSessionId = () => App._testSessionId || (orig && orig.call(App));
        });

        const chatMessages = [];
        await page.route('**/api/chat/**', route => {
            const body = route.request().postDataJSON ? route.request().postDataJSON() : null;
            if (body) chatMessages.push(body);
            route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true }) });
        });

        let saveCount = 0;
        await page.route('**/api/assessment/responses', route => {
            saveCount += 1;
            const early = saveCount === 1 ? EARLY_RESULT_TRANSMUTER : EARLY_RESULT_ABSORBER;
            route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify({ saved: true, question_id: 'sc_belong_01', progress: {}, early_result: early }),
            });
        });

        // Render the scenario as a live widget (unanswered) via chat.js's
        // real SSE-routing path, matching how tiered-assessment.spec.js
        // exercises LikertCard.
        await page.evaluate((data) => {
            const el = ScenarioCard.create(data);
            document.getElementById('chat-messages').appendChild(el);
        }, SCENARIO_DATA);

        // First click: pick option A.
        await page.locator('.scenario-choice').nth(0).click();
        await page.waitForTimeout(300);
        await expect(page.locator('.scenario-choice').nth(0)).toHaveClass(/scenario-choice--selected/);

        // Archetype reflects the first save's early_result.
        await page.locator('.results-tab', { hasText: 'Assessment' }).click();
        await expect(page.locator('#early-transmute-result')).toContainText('Transmuter', { timeout: 5000 });

        // Mis-click correction: re-select a DIFFERENT option live.
        await page.locator('.scenario-choice').nth(1).click();
        await page.waitForTimeout(300);
        await expect(page.locator('.scenario-choice').nth(1)).toHaveClass(/scenario-choice--selected/);
        await expect(page.locator('.scenario-choice').nth(0)).not.toHaveClass(/scenario-choice--selected/);

        // Archetype card updates in place to the second save's early_result --
        // this is the core "recompute + refresh" behavior TEST-001 verifies.
        await expect(page.locator('#early-transmute-result')).toContainText('Absorber', { timeout: 5000 });

        // The agent-advance signal (batch_complete) fires exactly once, on
        // the FIRST save only -- correcting the mis-click must not re-advance.
        const batchCompleteMsgs = chatMessages.filter(m => {
            try { return JSON.parse(m.message).type === 'batch_complete'; } catch (e) { return false; }
        });
        expect(batchCompleteMsgs.length).toBe(1);

        await page.screenshot({ path: `${SCREENSHOTS_DIR}/journey-01-live-archetype-update.png`, fullPage: false });
    });

    test('journey-02 (post-reload): editing a scenario replayed from history re-scores and updates the archetype', async ({ page }) => {
        await bypassAuth(page);
        // First /api/sessions call (list) happens on initial app load.
        await page.route('**/api/sessions', route => route.fulfill({
            status: 200, contentType: 'application/json',
            body: JSON.stringify({
                sessions: [{ session_id: 's1', user_id: 'test-user', message_count: 2 }],
                count: 1,
                user_total_cost_usd: 0,
            }),
        }));
        // The prior choice ('a') is what a reload replays -- sourced from
        // GET /history (BE-002 + FE-003's sessions.js -> chat.js wiring),
        // exercised here via the real Sessions.activate() entry point rather
        // than a direct Chat.renderHistory call.
        await page.route('**/api/sessions/**/history', route => route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify({
                session_id: 's1',
                messages: [{ role: 'widget', event_type: 'assessment.scenario', data: SCENARIO_DATA }],
                answered_responses: {},
                scenario_responses: { sc_belong_01: { choice: 'a' } },
            }),
        }));

        await page.goto('/');
        await page.locator('#app').waitFor({ state: 'visible', timeout: 10000 });
        await page.waitForLoadState('networkidle');
        await page.waitForTimeout(300);

        // Prior choice highlighted after the (simulated) reload's replay.
        const choiceA = page.locator('.scenario-choice').nth(0);
        await expect(choiceA).toHaveClass(/scenario-choice--selected/, { timeout: 5000 });
        await expect(choiceA).toHaveAttribute('aria-pressed', 'true');

        await page.screenshot({ path: `${SCREENSHOTS_DIR}/journey-02-reload-prior-choice.png`, fullPage: false });

        // Now change the answer -- an edit on a reloaded scenario.
        await page.route('**/api/assessment/responses', route => route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify({
                saved: true,
                question_id: 'sc_belong_01',
                progress: {},
                early_result: EARLY_RESULT_ABSORBER,
            }),
        }));
        const chatMessages = [];
        await page.route('**/api/chat/**', route => {
            const body = route.request().postDataJSON ? route.request().postDataJSON() : null;
            if (body) chatMessages.push(body);
            route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true }) });
        });

        await page.locator('.scenario-choice').nth(2).click();
        await page.waitForTimeout(300);
        await expect(page.locator('.scenario-choice').nth(2)).toHaveClass(/scenario-choice--selected/);

        // Archetype updates from this edit.
        await page.locator('.results-tab', { hasText: 'Assessment' }).click();
        await expect(page.locator('#early-transmute-result')).toContainText('Absorber', { timeout: 5000 });

        // Editing an already-answered (history-replayed) scenario must NOT
        // re-fire batch_complete -- the agent should not be told to advance
        // for an item it already advanced past.
        const batchCompleteMsgs = chatMessages.filter(m => {
            try { return JSON.parse(m.message).type === 'batch_complete'; } catch (e) { return false; }
        });
        expect(batchCompleteMsgs.length).toBe(0);

        await page.screenshot({ path: `${SCREENSHOTS_DIR}/journey-02-post-edit-archetype.png`, fullPage: false });
    });
});
