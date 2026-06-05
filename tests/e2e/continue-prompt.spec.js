// @ts-check
/**
 * E2E spec for the education.continue "Continue" button widget.
 *
 * Uses the Chat.renderHistory path (mock-driven, no live server needed) to
 * inject education.continue widget messages and verify:
 *   - The button renders with the agent-supplied label
 *   - Clicking it removes the button from the DOM (it disappears)
 *   - Clicking it calls Chat.sendMessage with the agent-supplied message
 *     (and does NOT render a user-message bubble)
 *   - A sparse payload (missing label/message) still renders a default
 *     "Continue" button without throwing
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

test.describe('Continue prompt widget (education.continue)', () => {

    test.beforeEach(async ({ page }) => {
        page._jsErrors = [];
        page.on('pageerror', err => page._jsErrors.push(err.message));

        await bypassAuth(page);
        await page.goto('/');
        await page.locator('#app').waitFor({ state: 'visible', timeout: 10000 });
        await page.waitForLoadState('networkidle');

        // Ensure a session id is available and spy on Chat.sendMessage so we can
        // assert what the button sends without depending on the live SSE stream.
        await page.evaluate(() => {
            if (typeof App !== 'undefined') {
                App._testSessionId = 's1';
                const origGet = App.getCurrentSessionId;
                App.getCurrentSessionId = () => App._testSessionId || (origGet && origGet.call(App));
            }
            window._sentMessages = [];
            if (typeof Chat !== 'undefined') {
                Chat.sendMessage = (sessionId, message) => {
                    window._sentMessages.push({ sessionId, message });
                    return Promise.resolve();
                };
            }
        });
    });

    test.afterEach(async ({ page }) => {
        const errs = page._jsErrors || [];
        if (errs.length > 0) throw new Error('Uncaught JS errors: ' + errs.join('; '));
    });

    // ── Render ───────────────────────────────────────────────────────────────

    test('cont-01: education.continue renders a button with the agent label', async ({ page }) => {
        await page.evaluate(() => {
            Chat.renderHistory([
                {
                    role: 'widget',
                    event_type: 'education.continue',
                    data: { label: 'Continue to Category 2: Your Score', message: 'Yes, continue to Category 2' },
                },
            ]);
        });

        const btn = page.locator('.continue-prompt-btn');
        await expect(btn).toBeVisible({ timeout: 5000 });
        await expect(btn).toHaveCount(1);
        await expect(btn).toContainText('Continue to Category 2: Your Score');

        await page.screenshot({ path: `${SCREENSHOTS_DIR}/cont-01-render.png` });
    });

    // ── Click: sends message + button disappears ──────────────────────────────

    test('cont-02: clicking the button sends the message and removes the button', async ({ page }) => {
        await page.evaluate(() => {
            Chat.renderHistory([
                {
                    role: 'widget',
                    event_type: 'education.continue',
                    data: { label: 'Continue', message: 'Yes, continue to Category 2' },
                },
            ]);
        });

        const btn = page.locator('.continue-prompt-btn');
        await expect(btn).toBeVisible({ timeout: 5000 });
        await btn.click();

        // Button disappears from the DOM
        await expect(page.locator('.continue-prompt-btn')).toHaveCount(0);
        await expect(page.locator('.continue-prompt')).toHaveCount(0);

        // Chat.sendMessage was called with the agent-supplied message
        const sent = await page.evaluate(() => window._sentMessages);
        expect(sent).toHaveLength(1);
        expect(sent[0].sessionId).toBe('s1');
        expect(sent[0].message).toBe('Yes, continue to Category 2');

        // No user-message bubble is rendered for the silent continue send
        await expect(page.locator('.chat-msg--user')).toHaveCount(0);

        await page.screenshot({ path: `${SCREENSHOTS_DIR}/cont-02-after-click.png` });
    });

    // ── Sparse payload falls back to default label, no throw ───────────────────

    test('cont-03: sparse payload (no label/message) renders a default Continue button', async ({ page }) => {
        await page.evaluate(() => {
            Chat.renderHistory([
                {
                    role: 'widget',
                    event_type: 'education.continue',
                    data: {},
                },
            ]);
        });

        const btn = page.locator('.continue-prompt-btn');
        await expect(btn).toBeVisible({ timeout: 5000 });
        await expect(btn).toContainText('Continue');

        await btn.click();
        const sent = await page.evaluate(() => window._sentMessages);
        expect(sent).toHaveLength(1);
        expect(sent[0].message).toBe('continue');
    });
});
