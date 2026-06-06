// @ts-check
/**
 * E2E for the LLM call history dialog (FE-001).
 *
 * Mock-driven (no live backend), mirroring cost-display.spec.js.
 *
 * Covers:
 *  - dialog opens when #cost-display button is clicked
 *  - dialog shows loading state then renders rows
 *  - empty state shown when no items returned
 *  - error state shown on fetch failure + toast
 *  - dialog closes via close button, Esc key, backdrop click
 *  - focus returns to #cost-display after close
 *  - pagination: "Load more" button fetches next page
 *  - ARIA attributes: role=dialog, aria-modal, aria-labelledby
 *  - XSS prevention: cell text set via textContent not innerHTML
 */
const { test, expect } = require('@playwright/test');

const SESSION_ID = 'usage-sess-1';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async function bypassAuth(page) {
    await page.route('**/auth/me', route => route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
            user_id: 'test-user', name: 'Test', email: 'test@example.com', current_phase: 'orientation',
        }),
    }));
    await page.route('**/api/sessions', route => route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
            sessions: [{ session_id: SESSION_ID, user_id: 'test-user', app_name: 'transmutation', archived: false, message_count: 0 }],
            count: 1,
            user_total_cost_usd: 1.23,
        }),
    }));
    await page.route('**/api/sessions/**/history', route => route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ session_id: SESSION_ID, messages: [], answered_responses: {} }),
    }));
    await page.route('**/api/results/**', route => route.fulfill({
        status: 200, contentType: 'application/json', body: JSON.stringify({}),
    }));
}

function makeLlmCallItem(id, description = 'Assessment agent · scoring') {
    return {
        session_id: `sess-${id}`,
        author: 'assessment_agent',
        phase: 'assessment',
        description,
        model_id: 'gemini-1.5-flash',
        input_tokens: 100 * id,
        output_tokens: 50 * id,
        cost_usd: 0.001 * id,
        created_at: `2026-06-05T14:${String(id).padStart(2, '0')}:00`,
    };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test.describe('LLM Call History Dialog', () => {
    test.beforeEach(async ({ page }) => {
        page._jsErrors = [];
        page.on('pageerror', err => page._jsErrors.push(err.message));
    });

    test.afterEach(async ({ page }) => {
        const errs = page._jsErrors || [];
        if (errs.length > 0) throw new Error('Uncaught JS errors: ' + errs.join('; '));
    });

    // ---- Dialog open / ARIA structure ----

    test('usage-01: #cost-display is a button that opens the dialog', async ({ page }) => {
        await bypassAuth(page);
        await page.route('**/api/usage/llm-calls**', route => route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify({ items: [], next_cursor: null, has_more: false }),
        }));
        await page.goto('/');
        await page.locator('#app').waitFor({ state: 'visible' });

        const btn = page.locator('#cost-display');
        await expect(btn).toHaveAttribute('type', 'button');
        await expect(page.locator('#cost-dialog')).toBeHidden();

        await btn.click({ force: true });
        await expect(page.locator('#cost-dialog')).toBeVisible({ timeout: 5000 });
    });

    test('usage-02: dialog has correct ARIA attributes', async ({ page }) => {
        await bypassAuth(page);
        await page.route('**/api/usage/llm-calls**', route => route.fulfill({
            status: 200, contentType: 'application/json',
            body: JSON.stringify({ items: [], next_cursor: null, has_more: false }),
        }));
        await page.goto('/');
        await page.locator('#app').waitFor({ state: 'visible' });
        await page.locator('#cost-display').click({ force: true });

        const dialog = page.locator('#cost-dialog');
        await expect(dialog).toHaveAttribute('role', 'dialog');
        await expect(dialog).toHaveAttribute('aria-modal', 'true');
        await expect(dialog).toHaveAttribute('aria-labelledby', 'cost-dialog-title');
    });

    // ---- Loading and rendering ----

    test('usage-03: dialog shows loading spinner while fetching', async ({ page }) => {
        await bypassAuth(page);
        let resolveRoute;
        await page.route('**/api/usage/llm-calls**', route => {
            resolveRoute = () => route.fulfill({
                status: 200, contentType: 'application/json',
                body: JSON.stringify({ items: [makeLlmCallItem(1)], next_cursor: null, has_more: false }),
            });
            // Don't fulfill immediately — simulate loading
        });
        await page.goto('/');
        await page.locator('#app').waitFor({ state: 'visible' });
        await page.locator('#cost-display').click({ force: true });

        // Spinner should be visible while request is pending
        await expect(page.locator('#cost-dialog-loading')).toBeVisible({ timeout: 2000 });

        // Fulfill the request
        resolveRoute();
        await expect(page.locator('#cost-dialog-loading')).toBeHidden({ timeout: 3000 });
    });

    test('usage-04: dialog renders call rows with correct data', async ({ page }) => {
        await bypassAuth(page);
        const item = makeLlmCallItem(1, 'Assessment agent · scoring your responses');
        await page.route('**/api/usage/llm-calls**', route => route.fulfill({
            status: 200, contentType: 'application/json',
            body: JSON.stringify({ items: [item], next_cursor: null, has_more: false }),
        }));
        await page.goto('/');
        await page.locator('#app').waitFor({ state: 'visible' });
        await page.locator('#cost-display').click({ force: true });

        const tbody = page.locator('#cost-dialog-tbody');
        await expect(tbody).toBeVisible({ timeout: 3000 });
        const rows = tbody.locator('tr');
        await expect(rows).toHaveCount(1);
        await expect(rows.first()).toContainText('Assessment agent · scoring your responses');
        await expect(rows.first()).toContainText('100');  // input_tokens
    });

    // ---- Empty state ----

    test('usage-05: dialog shows empty state when no calls exist', async ({ page }) => {
        await bypassAuth(page);
        await page.route('**/api/usage/llm-calls**', route => route.fulfill({
            status: 200, contentType: 'application/json',
            body: JSON.stringify({ items: [], next_cursor: null, has_more: false }),
        }));
        await page.goto('/');
        await page.locator('#app').waitFor({ state: 'visible' });
        await page.locator('#cost-display').click({ force: true });

        await expect(page.locator('#cost-dialog-empty')).toBeVisible({ timeout: 3000 });
        await expect(page.locator('#cost-dialog-empty')).toContainText('No LLM calls');
    });

    // ---- Error state ----

    test('usage-06: dialog shows error state on fetch failure', async ({ page }) => {
        await bypassAuth(page);
        await page.route('**/api/usage/llm-calls**', route => route.fulfill({
            status: 500, contentType: 'application/json',
            body: JSON.stringify({ detail: 'Internal Server Error' }),
        }));
        await page.goto('/');
        await page.locator('#app').waitFor({ state: 'visible' });
        await page.locator('#cost-display').click({ force: true });

        await expect(page.locator('#cost-dialog-error')).toBeVisible({ timeout: 3000 });
    });

    // ---- Close behaviors ----

    test('usage-07: dialog closes via close button', async ({ page }) => {
        await bypassAuth(page);
        await page.route('**/api/usage/llm-calls**', route => route.fulfill({
            status: 200, contentType: 'application/json',
            body: JSON.stringify({ items: [], next_cursor: null, has_more: false }),
        }));
        await page.goto('/');
        await page.locator('#app').waitFor({ state: 'visible' });
        await page.locator('#cost-display').click({ force: true });
        await expect(page.locator('#cost-dialog')).toBeVisible();

        await page.locator('#cost-dialog-close').click();
        await expect(page.locator('#cost-dialog')).toBeHidden();
    });

    test('usage-08: dialog closes on Esc key', async ({ page }) => {
        await bypassAuth(page);
        await page.route('**/api/usage/llm-calls**', route => route.fulfill({
            status: 200, contentType: 'application/json',
            body: JSON.stringify({ items: [makeLlmCallItem(1)], next_cursor: null, has_more: false }),
        }));
        await page.goto('/');
        await page.locator('#app').waitFor({ state: 'visible' });
        await page.locator('#cost-display').click({ force: true });
        await expect(page.locator('#cost-dialog')).toBeVisible({ timeout: 5000 });

        await page.keyboard.press('Escape');
        await expect(page.locator('#cost-dialog')).toBeHidden();
    });

    test('usage-09: dialog closes on backdrop click', async ({ page }) => {
        await bypassAuth(page);
        await page.route('**/api/usage/llm-calls**', route => route.fulfill({
            status: 200, contentType: 'application/json',
            body: JSON.stringify({ items: [makeLlmCallItem(1)], next_cursor: null, has_more: false }),
        }));
        await page.goto('/');
        await page.locator('#app').waitFor({ state: 'visible' });
        await page.locator('#cost-display').click({ force: true });
        await expect(page.locator('#cost-dialog')).toBeVisible({ timeout: 5000 });

        // Click the top-left of the backdrop (outside the centered dialog)
        await page.locator('#cost-dialog-backdrop').click({ position: { x: 5, y: 5 } });
        await expect(page.locator('#cost-dialog')).toBeHidden();
    });

    test('usage-10: focus returns to #cost-display after close', async ({ page }) => {
        await bypassAuth(page);
        await page.route('**/api/usage/llm-calls**', route => route.fulfill({
            status: 200, contentType: 'application/json',
            body: JSON.stringify({ items: [], next_cursor: null, has_more: false }),
        }));
        await page.goto('/');
        await page.locator('#app').waitFor({ state: 'visible' });
        await page.locator('#cost-display').click({ force: true });

        await page.locator('#cost-dialog-close').click();
        await expect(page.locator('#cost-dialog')).toBeHidden();

        // After close, #cost-display should have focus
        const focused = await page.evaluate(() => document.activeElement?.id);
        expect(focused).toBe('cost-display');
    });

    // ---- Pagination ----

    test('usage-11: Load more button appears when has_more is true', async ({ page }) => {
        await bypassAuth(page);
        await page.route('**/api/usage/llm-calls**', route => route.fulfill({
            status: 200, contentType: 'application/json',
            body: JSON.stringify({
                items: [makeLlmCallItem(1)],
                next_cursor: '42',
                has_more: true,
            }),
        }));
        await page.goto('/');
        await page.locator('#app').waitFor({ state: 'visible' });
        await page.locator('#cost-display').click({ force: true });

        await expect(page.locator('#cost-dialog-load-more')).toBeVisible({ timeout: 3000 });
    });

    test('usage-12: Load more appends new rows', async ({ page }) => {
        await bypassAuth(page);
        let callCount = 0;
        await page.route('**/api/usage/llm-calls**', route => {
            callCount++;
            if (callCount === 1) {
                route.fulfill({
                    status: 200, contentType: 'application/json',
                    body: JSON.stringify({
                        items: [makeLlmCallItem(1, 'Page 1 item')],
                        next_cursor: '100',
                        has_more: true,
                    }),
                });
            } else {
                route.fulfill({
                    status: 200, contentType: 'application/json',
                    body: JSON.stringify({
                        items: [makeLlmCallItem(2, 'Page 2 item')],
                        next_cursor: null,
                        has_more: false,
                    }),
                });
            }
        });
        await page.goto('/');
        await page.locator('#app').waitFor({ state: 'visible' });
        await page.locator('#cost-display').click({ force: true });

        // Wait for first page
        await expect(page.locator('#cost-dialog-tbody tr')).toHaveCount(1, { timeout: 3000 });
        await expect(page.locator('#cost-dialog-load-more')).toBeVisible();

        // Load second page
        await page.locator('#cost-dialog-load-more').click();
        await expect(page.locator('#cost-dialog-tbody tr')).toHaveCount(2, { timeout: 3000 });
        await expect(page.locator('#cost-dialog-load-more')).toBeHidden();
    });

    // ---- XSS prevention ----

    test('usage-13: cell content is text-safe (no innerHTML injection)', async ({ page }) => {
        await bypassAuth(page);
        const xssDescription = '<img src=x onerror=alert(1)>';
        await page.route('**/api/usage/llm-calls**', route => route.fulfill({
            status: 200, contentType: 'application/json',
            body: JSON.stringify({
                items: [{ ...makeLlmCallItem(1), description: xssDescription }],
                next_cursor: null,
                has_more: false,
            }),
        }));
        await page.goto('/');
        await page.locator('#app').waitFor({ state: 'visible' });
        await page.locator('#cost-display').click({ force: true });

        const tbody = page.locator('#cost-dialog-tbody');
        await expect(tbody).toBeVisible({ timeout: 3000 });
        // The img tag should appear as literal text, not parsed HTML
        const innerHtml = await tbody.innerHTML();
        // The <img should be escaped, not a real element
        expect(innerHtml).not.toContain('<img src=x');
        // The literal text should be present as text content
        await expect(tbody).toContainText('<img src=x');
    });
});
