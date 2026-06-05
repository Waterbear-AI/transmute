// @ts-check
/**
 * E2E spec for the education.comprehension widget guard (FE-001).
 *
 * Uses the Chat.renderHistory path (mock-driven, no live server needed) to
 * inject education.comprehension widget messages and verify:
 *   - WITH options:    .structured-option buttons render and a click sends
 *                      a comprehension_answer message
 *   - WITHOUT options: no .widget-card / .structured-option is rendered
 *                      (prevents empty card from record_comprehension_answer
 *                      feedback events that share the same event_type)
 */
const { test, expect } = require('@playwright/test');
const path = require('path');

const SCREENSHOTS_DIR = path.join(__dirname, 'screenshots');

async function bypassAuth(page) {
    await page.route('**/auth/me', route => route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
            user_id: 'test-user',
            name: 'Test User',
            email: 'test@example.com',
            current_phase: 'education',
        }),
    }));
    await page.route('**/api/sessions', route => route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
            sessions: [{ session_id: 's1', user_id: 'test-user', message_count: 0 }],
            count: 1,
            user_total_cost_usd: 0,
        }),
    }));
    await page.route('**/api/sessions/**/history', route => route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ session_id: 's1', messages: [], answered_responses: {} }),
    }));
    await page.route('**/api/results/**', route => route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({}),
    }));
}

test.describe('Comprehension widget guard (FE-001)', () => {

    test.beforeEach(async ({ page }) => {
        page._jsErrors = [];
        page.on('pageerror', err => page._jsErrors.push(err.message));

        await bypassAuth(page);
        await page.goto('/');
        await page.locator('#app').waitFor({ state: 'visible', timeout: 10000 });
        await page.waitForLoadState('networkidle');
    });

    test.afterEach(async ({ page }) => {
        const errs = page._jsErrors || [];
        if (errs.length > 0) throw new Error('Uncaught JS errors: ' + errs.join('; '));
    });

    // ── WITH options: widget should render ──────────────────────────────────

    test('comp-01: education.comprehension WITH options renders .structured-option buttons', async ({ page }) => {
        await page.evaluate(() => {
            Chat.renderHistory([
                {
                    role: 'widget',
                    event_type: 'education.comprehension',
                    data: {
                        dimension: 'Emotional Awareness',
                        category: 'what_this_means',
                        question_id: 'cc_ea_cat1_q1',
                        stem: 'What is Emotional Awareness?',
                        options: [
                            { key: 'a', text: 'Option A text' },
                            { key: 'b', text: 'Option B text' },
                            { key: 'c', text: 'Option C text' },
                            { key: 'd', text: 'Option D text' },
                        ],
                    },
                },
            ]);
        });

        // Widget card must appear
        const card = page.locator('.widget-card').first();
        await expect(card).toBeVisible({ timeout: 5000 });

        // All four option buttons must render
        const buttons = page.locator('.structured-option');
        await expect(buttons).toHaveCount(4);

        // Buttons must contain the option text
        await expect(buttons.nth(0)).toContainText('Option A text');
        await expect(buttons.nth(1)).toContainText('Option B text');

        await page.screenshot({
            path: `${SCREENSHOTS_DIR}/comp-01-with-options.png`,
        });
    });

    test('comp-02: clicking a .structured-option sends comprehension_answer via Chat.sendMessage', async ({ page }) => {
        // Intercept the chat POST that sendMessage triggers
        const sentMessages = [];
        await page.route('**/api/sessions/**/messages', route => {
            route.request().postDataJSON && sentMessages.push(route.request().postDataJSON());
            route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true }) });
        });

        // Also stub App.getCurrentSessionId to return a session so sendMessage fires
        await page.evaluate(() => {
            // Provide a minimal stub if App is not already set with a session
            if (typeof App !== 'undefined' && !App.getCurrentSessionId()) {
                App._testSessionId = 's1';
                const orig = App.getCurrentSessionId;
                App.getCurrentSessionId = () => App._testSessionId || (orig && orig.call(App));
            }
        });

        await page.evaluate(() => {
            Chat.renderHistory([
                {
                    role: 'widget',
                    event_type: 'education.comprehension',
                    data: {
                        dimension: 'Emotional Awareness',
                        category: 'what_this_means',
                        question_id: 'cc_ea_cat1_q1',
                        stem: 'What is Emotional Awareness?',
                        options: [
                            { key: 'a', text: 'Option A' },
                            { key: 'b', text: 'Option B' },
                        ],
                    },
                },
            ]);
        });

        const firstBtn = page.locator('.structured-option').first();
        await expect(firstBtn).toBeVisible({ timeout: 5000 });
        await firstBtn.click();

        // After clicking, button should be marked selected and all disabled
        await expect(firstBtn).toHaveClass(/structured-option--selected/);
        const allBtns = page.locator('.structured-option');
        for (let i = 0; i < await allBtns.count(); i++) {
            await expect(allBtns.nth(i)).toBeDisabled();
        }

        await page.screenshot({
            path: `${SCREENSHOTS_DIR}/comp-02-after-click.png`,
        });
    });

    // ── WITHOUT options: no widget card should render ────────────────────────

    test('comp-03: education.comprehension WITHOUT options does NOT render a widget card', async ({ page }) => {
        // This simulates the record_comprehension_answer feedback event which
        // carries event_type "education.comprehension" but no options array.
        await page.evaluate(() => {
            Chat.renderHistory([
                {
                    role: 'widget',
                    event_type: 'education.comprehension',
                    data: {
                        event_type: 'education.comprehension',
                        correct: true,
                        explanation: 'Great job!',
                        dimension: 'Emotional Awareness',
                        category: 'what_this_means',
                        question_id: 'cc_ea_cat1_q1',
                        // No "options" field — this is the feedback payload
                    },
                },
            ]);
        });

        // No widget card or option buttons should appear
        await expect(page.locator('.widget-card')).toHaveCount(0);
        await expect(page.locator('.structured-option')).toHaveCount(0);

        await page.screenshot({
            path: `${SCREENSHOTS_DIR}/comp-03-no-options-no-card.png`,
        });
    });

    test('comp-04: education.comprehension with EMPTY options array does NOT render a widget card', async ({ page }) => {
        await page.evaluate(() => {
            Chat.renderHistory([
                {
                    role: 'widget',
                    event_type: 'education.comprehension',
                    data: {
                        dimension: 'Emotional Awareness',
                        category: 'what_this_means',
                        question_id: 'cc_ea_cat1_q1',
                        stem: 'Empty options test',
                        options: [],  // explicitly empty array
                    },
                },
            ]);
        });

        await expect(page.locator('.widget-card')).toHaveCount(0);
        await expect(page.locator('.structured-option')).toHaveCount(0);

        await page.screenshot({
            path: `${SCREENSHOTS_DIR}/comp-04-empty-options-no-card.png`,
        });
    });

    // ── Mixed history: only events with options should produce cards ─────────

    test('comp-05: mixed history renders card only for event with valid options', async ({ page }) => {
        await page.evaluate(() => {
            Chat.renderHistory([
                // Feedback event (no options) — should NOT produce a card
                {
                    role: 'widget',
                    event_type: 'education.comprehension',
                    data: {
                        correct: false,
                        explanation: 'Not quite.',
                        dimension: 'Emotional Awareness',
                        category: 'what_this_means',
                        question_id: 'cc_ea_cat1_q1',
                    },
                },
                // Question event (with options) — SHOULD produce a card
                {
                    role: 'widget',
                    event_type: 'education.comprehension',
                    data: {
                        dimension: 'Emotional Awareness',
                        category: 'what_this_means',
                        question_id: 'cc_ea_cat1_q2',
                        stem: 'Second question',
                        options: [
                            { key: 'a', text: 'Choice A' },
                            { key: 'b', text: 'Choice B' },
                        ],
                    },
                },
            ]);
        });

        // Exactly one widget card from the second event
        await expect(page.locator('.widget-card')).toHaveCount(1);
        await expect(page.locator('.structured-option')).toHaveCount(2);

        await page.screenshot({
            path: `${SCREENSHOTS_DIR}/comp-05-mixed-history.png`,
        });
    });
});
