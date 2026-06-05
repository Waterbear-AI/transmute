// @ts-check
/**
 * E2E spec for control-message suppression in chat history rehydration.
 *
 * Widgets (StructuredChoice, LikertCard) send machine payloads to the agent
 * via Chat.sendMessage(JSON.stringify({type: 'comprehension_answer', ...})).
 * Live, those never render a user bubble. But on reload they come back as
 * role:'user' history rows — and used to render as raw JSON bubbles. This
 * spec verifies they are suppressed while ordinary text messages still show.
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

test.describe('History control-message suppression', () => {

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

    test('hist-01: comprehension_answer control JSON is NOT rendered as a user bubble', async ({ page }) => {
        await page.evaluate(() => {
            Chat.renderHistory([
                { role: 'agent', text: "There's your question — which option feels right?" },
                {
                    role: 'user',
                    text: JSON.stringify({
                        type: 'comprehension_answer',
                        question_id: 'cc_ea_cat1_q1',
                        selected_key: 'b',
                    }),
                },
                { role: 'agent', text: '✅ Correct! Nice work.' },
            ]);
        });

        // No user bubble should exist at all (the only user row was control JSON)
        await expect(page.locator('.chat-msg--user')).toHaveCount(0);

        // And the raw JSON string must not appear anywhere in the transcript
        await expect(page.locator('.chat-messages')).not.toContainText('comprehension_answer');

        // The agent messages around it still render
        await expect(page.locator('.chat-msg--agent')).toHaveCount(2);

        await page.screenshot({ path: `${SCREENSHOTS_DIR}/hist-01-suppressed.png` });
    });

    test('hist-02: batch_complete control JSON is also suppressed', async ({ page }) => {
        await page.evaluate(() => {
            Chat.renderHistory([
                { role: 'user', text: JSON.stringify({ type: 'batch_complete', batch_id: 'b1' }) },
            ]);
        });
        await expect(page.locator('.chat-msg--user')).toHaveCount(0);
    });

    test('hist-03: ordinary text user messages still render (incl. plain text and non-control JSON)', async ({ page }) => {
        await page.evaluate(() => {
            Chat.renderHistory([
                { role: 'user', text: 'Yes, continue to Category 2: Your Score' },
                { role: 'user', text: 'I think emotions drive my filtering.' },
                // A JSON object WITHOUT a recognized control type must still show.
                { role: 'user', text: '{"note": "this is not a control message"}' },
            ]);
        });

        const userBubbles = page.locator('.chat-msg--user');
        await expect(userBubbles).toHaveCount(3);
        await expect(userBubbles.nth(0)).toContainText('continue to Category 2');
        await expect(userBubbles.nth(2)).toContainText('not a control message');
    });
});
