// @ts-check
/**
 * Screenshot capture for LLM call history dialog visual verification.
 */
const { test, expect } = require('@playwright/test');
const path = require('path');

const SESSION_ID = 'screenshot-sess-1';
const SCREENSHOTS_DIR = path.join(__dirname, 'screenshots');

function makeLlmCallItem(id, description = 'Assessment agent · scoring') {
    return {
        session_id: `sess-${id}`,
        author: 'assessment_agent',
        phase: 'assessment',
        description,
        model_id: 'gemini-1.5-flash',
        input_tokens: 1204 * id,
        output_tokens: 312 * id,
        cost_usd: 0.0121 * id,
        created_at: `2026-06-05T14:${String(id + 20).padStart(2, '0')}:00`,
    };
}

async function bypassAuth(page) {
    await page.route('**/auth/me', route => route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ user_id: 'test-user', name: 'Test User', email: 'test@example.com', current_phase: 'assessment' }),
    }));
    await page.route('**/api/sessions', route => route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({
            sessions: [{ session_id: SESSION_ID, user_id: 'test-user', app_name: 'transmutation', archived: false, message_count: 0 }],
            count: 1,
            user_total_cost_usd: 3.40,
        }),
    }));
    await page.route('**/api/sessions/**/history', route => route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ session_id: SESSION_ID, messages: [], answered_responses: {} }),
    }));
    await page.route('**/api/results/**', route => route.fulfill({
        status: 200, contentType: 'application/json', body: JSON.stringify({}),
    }));
}

test.describe('LLM Call History Dialog Screenshots', () => {
    test('screenshot-01: top bar with cost button (closed state)', async ({ page }) => {
        await bypassAuth(page);
        await page.route('**/api/usage/llm-calls**', route => route.fulfill({
            status: 200, contentType: 'application/json',
            body: JSON.stringify({ items: [], next_cursor: null, has_more: false }),
        }));
        await page.goto('/');
        await page.locator('#app').waitFor({ state: 'visible' });
        await page.screenshot({ path: `${SCREENSHOTS_DIR}/llm-call-history-01-topbar-closed.png`, fullPage: false });
    });

    test('screenshot-02: dialog open with rows', async ({ page }) => {
        await bypassAuth(page);
        await page.route('**/api/usage/llm-calls**', route => route.fulfill({
            status: 200, contentType: 'application/json',
            body: JSON.stringify({
                items: [
                    makeLlmCallItem(1, 'Assessment agent · scoring your responses'),
                    makeLlmCallItem(2, 'Education agent · education session'),
                    makeLlmCallItem(3, 'Profile agent · building your profile'),
                ],
                next_cursor: '99',
                has_more: true,
            }),
        }));
        await page.goto('/');
        await page.locator('#app').waitFor({ state: 'visible' });
        await page.locator('#cost-display').click({ force: true });
        await page.locator('#cost-dialog-tbody tr').first().waitFor({ state: 'visible', timeout: 5000 });
        await page.screenshot({ path: `${SCREENSHOTS_DIR}/llm-call-history-02-dialog-with-rows.png`, fullPage: false });
    });

    test('screenshot-03: dialog empty state', async ({ page }) => {
        await bypassAuth(page);
        await page.route('**/api/usage/llm-calls**', route => route.fulfill({
            status: 200, contentType: 'application/json',
            body: JSON.stringify({ items: [], next_cursor: null, has_more: false }),
        }));
        await page.goto('/');
        await page.locator('#app').waitFor({ state: 'visible' });
        await page.locator('#cost-display').click({ force: true });
        await page.locator('#cost-dialog-empty').waitFor({ state: 'visible', timeout: 5000 });
        await page.screenshot({ path: `${SCREENSHOTS_DIR}/llm-call-history-03-dialog-empty.png`, fullPage: false });
    });
});
