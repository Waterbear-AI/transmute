// @ts-check
/**
 * E2E for the top-bar lifetime LLM cost indicator.
 *
 * Verifies the user-visible additions:
 *  - on load, the top bar shows "Est. cost: $0.00 (total $X)" seeded from
 *    /api/sessions (before any chat turn);
 *  - the public Chat.seedCostTotal renderer formats session + lifetime;
 *  - no uncaught JS errors.
 *
 * Mock-driven (no live backend), mirroring regression-panel.spec.js.
 */
const { test, expect } = require('@playwright/test');

const SESSION_ID = 'cost-sess-1';

async function bypassAuthWithCost(page, userTotal) {
    await page.route('**/auth/me', route => route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ user_id: 'test-user', name: 'Test User', email: 'test@example.com', current_phase: 'orientation' }),
    }));
    // GET /api/sessions — carries the new lifetime total field.
    await page.route('**/api/sessions', route => route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
            sessions: [{ session_id: SESSION_ID, user_id: 'test-user', app_name: 'transmutation', archived: false, message_count: 0 }],
            count: 1,
            user_total_cost_usd: userTotal,
        }),
    }));
    // Session history (activate() fetches this) and results — empty.
    await page.route('**/api/sessions/**/history', route => route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ session_id: SESSION_ID, messages: [], answered_responses: {} }),
    }));
    await page.route('**/api/results/**', route => route.fulfill({
        status: 200, contentType: 'application/json', body: JSON.stringify({}),
    }));
}

test.describe('Top-bar lifetime cost', () => {
    test.beforeEach(async ({ page }) => {
        page._jsErrors = [];
        page.on('pageerror', err => page._jsErrors.push(err.message));
    });

    test.afterEach(async ({ page }) => {
        const errs = page._jsErrors || [];
        if (errs.length > 0) throw new Error('Uncaught JS errors: ' + errs.join('; '));
    });

    test('cost-01: lifetime total is shown on load, before any chat turn', async ({ page }) => {
        await bypassAuthWithCost(page, 3.47);
        await page.goto('/');
        await page.locator('#app').waitFor({ state: 'visible', timeout: 10000 });

        const cost = page.locator('#cost-display');
        await expect(cost).toContainText('total $3.47', { timeout: 5000 });
        // Session portion is $0.00 before the first message this session.
        await expect(cost).toContainText('$0.00');
    });

    test('cost-02: Chat.seedCostTotal renders session + lifetime format', async ({ page }) => {
        await bypassAuthWithCost(page, 0);
        await page.goto('/');
        await page.locator('#app').waitFor({ state: 'visible', timeout: 10000 });

        await page.evaluate(() => { if (typeof Chat !== 'undefined') Chat.seedCostTotal(9.99); });
        await expect(page.locator('#cost-display')).toHaveText('Est. cost: $0.00 (total $9.99)', { timeout: 5000 });
    });

    test('cost-03: zero lifetime total renders cleanly', async ({ page }) => {
        await bypassAuthWithCost(page, 0);
        await page.goto('/');
        await page.locator('#app').waitFor({ state: 'visible', timeout: 10000 });
        await expect(page.locator('#cost-display')).toContainText('total $0.00', { timeout: 5000 });
    });
});
