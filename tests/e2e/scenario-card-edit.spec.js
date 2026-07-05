// @ts-check
/**
 * E2E spec for FE-002: ScenarioCard editability + results refresh.
 *
 * Verifies the ScenarioCard.create(data, selectedChoice) contract added in
 * FE-002 (spec.md Required Implementation):
 *  - selectedChoice prefills/highlights the corresponding option on render
 *    (scenario-choice--selected + aria-pressed="true"), all options remain
 *    clickable
 *  - the agent-advance ("batch_complete") signal is NOT re-fired when editing
 *    an already-answered (selectedChoice-seeded) scenario
 *  - the agent-advance signal IS fired exactly once on a fresh (unanswered)
 *    scenario's first save
 *  - a save response carrying `early_result` calls Results.applyEarlyResult
 *    with that value so the results panel refreshes
 *  - a save response with no `early_result` (e.g. before Tier 1 completes)
 *    does not call Results.applyEarlyResult
 *  - keyboard operability: Enter/Space activates a focused choice
 *  - zero uncaught JS errors
 *
 * Test strategy: call ScenarioCard.create(...) directly via page.evaluate and
 * append the returned element into the page (matches how chat.js appends
 * widget cards) — this component isn't wired through Chat.renderHistory with
 * a selectedChoice yet (that's the separate history-replay task), so this
 * spec exercises the widget's public contract directly, the same way
 * apply-early-result.spec.js exercises Results directly.
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

const EARLY_RESULT_PAYLOAD = {
    event_type: 'assessment.transmute_result',
    archetype: 'transmuter',
    x: 0.31,
    y: 0.44,
    confidence: 'medium',
    confidence_reason: 'Based on ~18 core answers; a few more will sharpen it.',
    computed_at: '2026-07-05T00:00:00',
};

async function bypassAuth(page) {
    await page.route('**/auth/me', route => route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ user_id: 'test-user', name: 'Test User', email: 'test@example.com' }),
    }));
    await page.route('**/api/sessions', route => route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ sessions: [], count: 0, user_total_cost_usd: 0 }),
    }));
    await page.route('**/api/results/**', route => route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({}),
    }));
}

/** Append a ScenarioCard.create(data, selectedChoice) result into the DOM. */
async function renderScenarioCard(page, data, selectedChoice) {
    await page.evaluate(([d, sc]) => {
        const el = ScenarioCard.create(d, sc);
        document.body.appendChild(el);
    }, [data, selectedChoice ?? null]);
}

test.describe('ScenarioCard editability + results refresh (FE-002)', () => {

    test.beforeEach(async ({ page }) => {
        page._jsErrors = [];
        page.on('pageerror', err => page._jsErrors.push(err.message));

        await bypassAuth(page);
        await page.goto('/');
        await page.locator('#app').waitFor({ state: 'visible', timeout: 10000 });
        await page.waitForLoadState('networkidle');

        // Stub App.getCurrentSessionId so _notifyScenarioAnswered's
        // Chat.sendMessage call has a session to target.
        await page.evaluate(() => {
            App._testSessionId = 's1';
            const orig = App.getCurrentSessionId;
            App.getCurrentSessionId = () => App._testSessionId || (orig && orig.call(App));
        });
    });

    test.afterEach(async ({ page }) => {
        const errs = page._jsErrors || [];
        if (errs.length > 0) throw new Error('Uncaught JS errors: ' + errs.join('; '));
    });

    test('sc-01: selectedChoice prefills and highlights the prior option on render', async ({ page }) => {
        await renderScenarioCard(page, SCENARIO_DATA, 'b');

        const choiceB = page.locator('.scenario-choice').nth(1);
        await expect(choiceB).toHaveClass(/scenario-choice--selected/);
        await expect(choiceB).toHaveAttribute('aria-pressed', 'true');

        const choiceA = page.locator('.scenario-choice').nth(0);
        await expect(choiceA).not.toHaveClass(/scenario-choice--selected/);
        await expect(choiceA).toHaveAttribute('aria-pressed', 'false');
        // Still clickable -- editability, not a locked/disabled state.
        await expect(choiceA).toBeEnabled();

        await page.locator('.scenario-choices').scrollIntoViewIfNeeded();
        await page.screenshot({ path: `${SCREENSHOTS_DIR}/sc-01-prefilled-choice.png` });
    });

    test('sc-02: editing an already-answered scenario does not re-fire batch_complete', async ({ page }) => {
        const sentMessages = [];
        await page.route('**/api/chat/**', route => {
            const body = route.request().postDataJSON ? route.request().postDataJSON() : null;
            if (body) sentMessages.push(body);
            route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true }) });
        });
        await page.route('**/api/assessment/responses', route => route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify({ saved: true, question_id: 'sc_belong_01', progress: {}, early_result: null }),
        }));

        // Rendered already-answered (selectedChoice = 'a') -- edit to 'c'.
        await renderScenarioCard(page, SCENARIO_DATA, 'a');
        await page.locator('.scenario-choice').nth(2).click();
        await page.waitForTimeout(300);

        // New choice is now selected/saved...
        await expect(page.locator('.scenario-choice').nth(2)).toHaveClass(/scenario-choice--selected/);
        // ...but batch_complete must NOT have been sent (notified started true).
        const batchCompleteMsgs = sentMessages.filter(m => {
            try { return JSON.parse(m.message).type === 'batch_complete'; } catch (e) { return false; }
        });
        expect(batchCompleteMsgs.length).toBe(0);
    });

    test('sc-03: a fresh (unanswered) scenario fires batch_complete exactly once, even after re-selecting', async ({ page }) => {
        const sentMessages = [];
        await page.route('**/api/chat/**', route => {
            const body = route.request().postDataJSON ? route.request().postDataJSON() : null;
            if (body) sentMessages.push(body);
            route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true }) });
        });
        await page.route('**/api/assessment/responses', route => route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify({ saved: true, question_id: 'sc_belong_01', progress: {}, early_result: null }),
        }));

        // No selectedChoice -- fresh/unanswered.
        await renderScenarioCard(page, SCENARIO_DATA, null);
        await page.locator('.scenario-choice').nth(0).click();
        await page.waitForTimeout(200);
        // Correct a mis-click -- re-select a different option.
        await page.locator('.scenario-choice').nth(1).click();
        await page.waitForTimeout(200);

        const batchCompleteMsgs = sentMessages.filter(m => {
            try { return JSON.parse(m.message).type === 'batch_complete'; } catch (e) { return false; }
        });
        expect(batchCompleteMsgs.length).toBe(1);
    });

    test('sc-04: a save response carrying early_result calls Results.applyEarlyResult', async ({ page }) => {
        await page.route('**/api/assessment/responses', route => route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify({
                saved: true,
                question_id: 'sc_belong_01',
                progress: {},
                early_result: EARLY_RESULT_PAYLOAD,
            }),
        }));
        await page.route('**/api/chat/**', route => route.fulfill({
            status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true }),
        }));

        // Spy on Results.applyEarlyResult.
        await page.evaluate(() => {
            window.__appliedEarlyResults = [];
            const orig = Results.applyEarlyResult;
            Results.applyEarlyResult = (er) => { window.__appliedEarlyResults.push(er); return orig(er); };
        });

        await renderScenarioCard(page, SCENARIO_DATA, null);
        await page.locator('.scenario-choice').nth(0).click();
        await page.waitForTimeout(300);

        const applied = await page.evaluate(() => window.__appliedEarlyResults);
        expect(applied.length).toBe(1);
        expect(applied[0].archetype).toBe('transmuter');

        // And the results panel picked it up -- switch to Assessment tab and
        // confirm the card rendered (applyEarlyResult also calls _renderTabs()).
        await page.locator('.results-tab', { hasText: 'Assessment' }).click();
        await expect(page.locator('#early-transmute-result')).toBeVisible({ timeout: 5000 });

        await page.screenshot({ path: `${SCREENSHOTS_DIR}/sc-04-early-result-applied.png` });
    });

    test('sc-05: a save response with no early_result does not call Results.applyEarlyResult', async ({ page }) => {
        await page.route('**/api/assessment/responses', route => route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify({ saved: true, question_id: 'sc_belong_01', progress: {}, early_result: null }),
        }));
        await page.route('**/api/chat/**', route => route.fulfill({
            status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true }),
        }));

        await page.evaluate(() => {
            window.__appliedEarlyResults = [];
            const orig = Results.applyEarlyResult;
            Results.applyEarlyResult = (er) => { window.__appliedEarlyResults.push(er); return orig(er); };
        });

        await renderScenarioCard(page, SCENARIO_DATA, null);
        await page.locator('.scenario-choice').nth(0).click();
        await page.waitForTimeout(300);

        const applied = await page.evaluate(() => window.__appliedEarlyResults);
        expect(applied.length).toBe(0);
    });

    test('sc-06: Enter key activates a focused scenario choice', async ({ page }) => {
        await page.route('**/api/assessment/responses', route => route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify({ saved: true, question_id: 'sc_belong_01', progress: {}, early_result: null }),
        }));
        await page.route('**/api/chat/**', route => route.fulfill({
            status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true }),
        }));

        await renderScenarioCard(page, SCENARIO_DATA, null);
        const first = page.locator('.scenario-choice').nth(0);
        await first.focus();
        await page.keyboard.press('Enter');
        await page.waitForTimeout(200);

        await expect(first).toHaveClass(/scenario-choice--selected/);
        await expect(first).toHaveAttribute('aria-pressed', 'true');
    });

    test('sc-07: 0 JS errors across prefill + edit + early_result refresh flow', async ({ page }) => {
        await page.route('**/api/assessment/responses', route => route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify({
                saved: true,
                question_id: 'sc_belong_01',
                progress: {},
                early_result: EARLY_RESULT_PAYLOAD,
            }),
        }));
        await page.route('**/api/chat/**', route => route.fulfill({
            status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true }),
        }));

        await renderScenarioCard(page, SCENARIO_DATA, 'a');
        await page.locator('.scenario-choice').nth(1).click();
        await page.waitForTimeout(300);

        // afterEach asserts 0 pageerror events -- this test just exercises the flow.
        await expect(page.locator('.scenario-choice').nth(1)).toHaveClass(/scenario-choice--selected/);
    });
});
