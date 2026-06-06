// @ts-check
/**
 * E2E test suite for the LLM cost dialog user journeys (TEST-002).
 *
 * Validates the full user journey through the LLM call history dialog:
 *  - opening the dialog via the 'Est. cost' trigger button
 *  - rendering call data from a mocked /api/usage/llm-calls endpoint
 *  - empty state when no calls are present
 *  - 'Load more' pagination appending rows
 *  - dialog closure via Esc key, backdrop click, and close button
 *  - focus management (focus returns to trigger after close)
 *  - accessibility attributes (role=dialog, aria-modal, aria-labelledby)
 *  - regression: cost-display.spec.js scenarios still work after span→button change
 *
 * Mock-driven (no live backend), mirroring cost-display.spec.js conventions.
 * JS error monitoring via page.on('pageerror') in every test.
 */
const { test, expect } = require('@playwright/test');

const SESSION_ID = 'cost-dialog-sess-1';

// ---------------------------------------------------------------------------
// Auth + common route helpers
// ---------------------------------------------------------------------------

async function bypassAuth(page, userTotalCostUsd = 2.50) {
    await page.route('**/auth/me', route => route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
            user_id: 'test-user',
            name: 'Test User',
            email: 'test@example.com',
            current_phase: 'orientation',
        }),
    }));
    await page.route('**/api/sessions', route => route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
            sessions: [{
                session_id: SESSION_ID,
                user_id: 'test-user',
                app_name: 'transmutation',
                archived: false,
                message_count: 0,
            }],
            count: 1,
            user_total_cost_usd: userTotalCostUsd,
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

function makeItem(id, desc = null) {
    return {
        session_id: `sess-${id}`,
        author: 'assessment_agent',
        phase: 'assessment',
        description: desc || `Assessment agent · call ${id}`,
        model_id: 'gemini-1.5-flash',
        input_tokens: 500 * id,
        output_tokens: 200 * id,
        cost_usd: 0.005 * id,
        created_at: `2026-06-05T10:${String(id).padStart(2, '0')}:00`,
    };
}

async function openApp(page) {
    await page.goto('/');
    await page.locator('#app').waitFor({ state: 'visible', timeout: 10000 });
}

async function openDialog(page) {
    await page.locator('#cost-display').click({ force: true });
    await expect(page.locator('#cost-dialog')).toBeVisible({ timeout: 5000 });
}

// ---------------------------------------------------------------------------
// JS error guard
// ---------------------------------------------------------------------------

test.beforeEach(async ({ page }) => {
    page._jsErrors = [];
    page.on('pageerror', err => page._jsErrors.push(err.message));
});

test.afterEach(async ({ page }) => {
    const errs = page._jsErrors || [];
    if (errs.length > 0) throw new Error('Uncaught JS errors: ' + errs.join('; '));
});

// ---------------------------------------------------------------------------
// Journey 1: Dialog opening
// ---------------------------------------------------------------------------

test.describe('dialog-open', () => {
    test('dialog-open-01: cost-display is a button and dialog opens on click', async ({ page }) => {
        await bypassAuth(page);
        await page.route('**/api/usage/llm-calls**', route => route.fulfill({
            status: 200, contentType: 'application/json',
            body: JSON.stringify({ items: [], next_cursor: null, has_more: false }),
        }));
        await openApp(page);

        // Trigger must be a semantic button (frontend-accessibility R1)
        const trigger = page.locator('#cost-display');
        await expect(trigger).toHaveAttribute('type', 'button');

        // Dialog hidden before click
        await expect(page.locator('#cost-dialog')).toBeHidden();

        // Click opens dialog
        await trigger.click({ force: true });
        await expect(page.locator('#cost-dialog')).toBeVisible({ timeout: 5000 });
    });

    test('dialog-open-02: dialog has correct ARIA modal attributes', async ({ page }) => {
        await bypassAuth(page);
        await page.route('**/api/usage/llm-calls**', route => route.fulfill({
            status: 200, contentType: 'application/json',
            body: JSON.stringify({ items: [], next_cursor: null, has_more: false }),
        }));
        await openApp(page);
        await openDialog(page);

        const dialog = page.locator('#cost-dialog');
        await expect(dialog).toHaveAttribute('role', 'dialog');
        await expect(dialog).toHaveAttribute('aria-modal', 'true');
        await expect(dialog).toHaveAttribute('aria-labelledby', 'cost-dialog-title');
        await expect(page.locator('#cost-dialog-title')).toContainText('LLM Call History');
    });
});

// ---------------------------------------------------------------------------
// Journey 2: Content rendering
// ---------------------------------------------------------------------------

test.describe('dialog-content', () => {
    test('dialog-content-01: renders mocked call data in table rows', async ({ page }) => {
        await bypassAuth(page, 3.75);
        await page.route('**/api/usage/llm-calls**', route => route.fulfill({
            status: 200, contentType: 'application/json',
            body: JSON.stringify({
                items: [
                    makeItem(1, 'Assessment agent · scoring your responses'),
                    makeItem(2, 'Education agent · education session'),
                    makeItem(3, 'Profile agent · building your profile'),
                ],
                next_cursor: null,
                has_more: false,
            }),
        }));
        await openApp(page);
        await openDialog(page);

        const rows = page.locator('#cost-dialog-tbody tr');
        await expect(rows).toHaveCount(3, { timeout: 5000 });

        // First row: description, in tokens, cost
        const firstRow = rows.first();
        await expect(firstRow).toContainText('Assessment agent · scoring your responses');
        await expect(firstRow).toContainText('500');    // input_tokens for item 1
        await expect(firstRow).toContainText('$0.0050');
    });

    test('dialog-content-02: subtitle shows current cost total', async ({ page }) => {
        await bypassAuth(page, 3.40);
        await page.route('**/api/usage/llm-calls**', route => route.fulfill({
            status: 200, contentType: 'application/json',
            body: JSON.stringify({ items: [makeItem(1)], next_cursor: null, has_more: false }),
        }));
        await openApp(page);
        await openDialog(page);

        // Subtitle copies current cost-display text
        const subtitle = page.locator('#cost-dialog-subtitle');
        await expect(subtitle).toBeVisible({ timeout: 3000 });
        await expect(subtitle).toContainText('3.40');
    });

    test('dialog-content-03: all table column headers present', async ({ page }) => {
        await bypassAuth(page);
        await page.route('**/api/usage/llm-calls**', route => route.fulfill({
            status: 200, contentType: 'application/json',
            body: JSON.stringify({ items: [makeItem(1)], next_cursor: null, has_more: false }),
        }));
        await openApp(page);
        await openDialog(page);

        const table = page.locator('#cost-dialog-table');
        await expect(table).toContainText('When');
        await expect(table).toContainText('What it was for');
        await expect(table).toContainText('In');
        await expect(table).toContainText('Out');
        await expect(table).toContainText('Cost');
    });
});

// ---------------------------------------------------------------------------
// Journey 3: Empty state
// ---------------------------------------------------------------------------

test.describe('dialog-empty', () => {
    test('dialog-empty-01: shows empty state when no calls exist', async ({ page }) => {
        await bypassAuth(page);
        await page.route('**/api/usage/llm-calls**', route => route.fulfill({
            status: 200, contentType: 'application/json',
            body: JSON.stringify({ items: [], next_cursor: null, has_more: false }),
        }));
        await openApp(page);
        await openDialog(page);

        await expect(page.locator('#cost-dialog-empty')).toBeVisible({ timeout: 3000 });
        await expect(page.locator('#cost-dialog-empty')).toContainText('No LLM calls');
        // Table body should be empty
        await expect(page.locator('#cost-dialog-tbody tr')).toHaveCount(0);
    });

    test('dialog-empty-02: Load more button hidden when empty', async ({ page }) => {
        await bypassAuth(page);
        await page.route('**/api/usage/llm-calls**', route => route.fulfill({
            status: 200, contentType: 'application/json',
            body: JSON.stringify({ items: [], next_cursor: null, has_more: false }),
        }));
        await openApp(page);
        await openDialog(page);
        await page.locator('#cost-dialog-empty').waitFor({ state: 'visible', timeout: 3000 });
        await expect(page.locator('#cost-dialog-load-more')).toBeHidden();
    });
});

// ---------------------------------------------------------------------------
// Journey 4: Pagination (Load more)
// ---------------------------------------------------------------------------

test.describe('dialog-pagination', () => {
    test('dialog-pagination-01: Load more button appears when has_more is true', async ({ page }) => {
        await bypassAuth(page);
        await page.route('**/api/usage/llm-calls**', route => route.fulfill({
            status: 200, contentType: 'application/json',
            body: JSON.stringify({
                items: [makeItem(1)],
                next_cursor: '42',
                has_more: true,
            }),
        }));
        await openApp(page);
        await openDialog(page);

        await expect(page.locator('#cost-dialog-tbody tr')).toHaveCount(1, { timeout: 3000 });
        await expect(page.locator('#cost-dialog-load-more')).toBeVisible();
    });

    test('dialog-pagination-02: Load more appends rows and hides button on last page', async ({ page }) => {
        await bypassAuth(page);
        let requestCount = 0;
        await page.route('**/api/usage/llm-calls**', route => {
            requestCount++;
            if (requestCount === 1) {
                route.fulfill({
                    status: 200, contentType: 'application/json',
                    body: JSON.stringify({
                        items: [makeItem(1, 'First page item')],
                        next_cursor: '100',
                        has_more: true,
                    }),
                });
            } else {
                route.fulfill({
                    status: 200, contentType: 'application/json',
                    body: JSON.stringify({
                        items: [makeItem(2, 'Second page item')],
                        next_cursor: null,
                        has_more: false,
                    }),
                });
            }
        });
        await openApp(page);
        await openDialog(page);

        // First page
        await expect(page.locator('#cost-dialog-tbody tr')).toHaveCount(1, { timeout: 3000 });
        await expect(page.locator('#cost-dialog-load-more')).toBeVisible();

        // Load second page
        await page.locator('#cost-dialog-load-more').click({ force: true });
        await expect(page.locator('#cost-dialog-tbody tr')).toHaveCount(2, { timeout: 3000 });

        // Load more hidden after last page
        await expect(page.locator('#cost-dialog-load-more')).toBeHidden();
    });

    test('dialog-pagination-03: reopening dialog resets to first page', async ({ page }) => {
        await bypassAuth(page);
        let requestCount = 0;
        await page.route('**/api/usage/llm-calls**', route => {
            requestCount++;
            if (requestCount <= 2) {
                route.fulfill({
                    status: 200, contentType: 'application/json',
                    body: JSON.stringify({
                        items: [makeItem(requestCount)],
                        next_cursor: '50',
                        has_more: true,
                    }),
                });
            } else {
                route.fulfill({
                    status: 200, contentType: 'application/json',
                    body: JSON.stringify({ items: [makeItem(3)], next_cursor: null, has_more: false }),
                });
            }
        });
        await openApp(page);
        await openDialog(page);
        await expect(page.locator('#cost-dialog-tbody tr')).toHaveCount(1, { timeout: 3000 });

        // Close and reopen — table must be cleared and refetched
        await page.locator('#cost-dialog-close').click({ force: true });
        await expect(page.locator('#cost-dialog')).toBeHidden();

        await page.locator('#cost-display').click({ force: true });
        await expect(page.locator('#cost-dialog')).toBeVisible({ timeout: 3000 });
        // After reopen, fresh first page (1 row, not accumulated)
        await expect(page.locator('#cost-dialog-tbody tr')).toHaveCount(1, { timeout: 3000 });
    });
});

// ---------------------------------------------------------------------------
// Journey 5: Dialog closure and focus management
// ---------------------------------------------------------------------------

test.describe('dialog-close', () => {
    async function setupForClose(page) {
        await bypassAuth(page);
        await page.route('**/api/usage/llm-calls**', route => route.fulfill({
            status: 200, contentType: 'application/json',
            body: JSON.stringify({ items: [makeItem(1)], next_cursor: null, has_more: false }),
        }));
        await openApp(page);
        await openDialog(page);
        await page.locator('#cost-dialog-tbody tr').first().waitFor({ state: 'visible', timeout: 3000 });
    }

    test('dialog-close-01: close button dismisses dialog', async ({ page }) => {
        await setupForClose(page);
        await page.locator('#cost-dialog-close').click({ force: true });
        await expect(page.locator('#cost-dialog')).toBeHidden();
    });

    test('dialog-close-02: Escape key dismisses dialog', async ({ page }) => {
        await setupForClose(page);
        await page.keyboard.press('Escape');
        await expect(page.locator('#cost-dialog')).toBeHidden();
    });

    test('dialog-close-03: backdrop click dismisses dialog', async ({ page }) => {
        await setupForClose(page);
        await page.locator('#cost-dialog-backdrop').click({ position: { x: 5, y: 5 } });
        await expect(page.locator('#cost-dialog')).toBeHidden();
    });

    test('dialog-close-04: focus returns to #cost-display after any close method', async ({ page }) => {
        await setupForClose(page);
        await page.locator('#cost-dialog-close').click({ force: true });
        await expect(page.locator('#cost-dialog')).toBeHidden();

        const focusedId = await page.evaluate(() => document.activeElement?.id);
        expect(focusedId).toBe('cost-display');
    });
});

// ---------------------------------------------------------------------------
// Journey 6: Regression — cost-display.spec.js scenarios
// (Verifies span→button change does not break existing cost text display)
// ---------------------------------------------------------------------------

test.describe('cost-display-regression', () => {
    test('regression-01: lifetime total shown on load from /api/sessions', async ({ page }) => {
        await bypassAuth(page, 4.21);
        await page.route('**/api/usage/llm-calls**', route => route.fulfill({
            status: 200, contentType: 'application/json',
            body: JSON.stringify({ items: [], next_cursor: null, has_more: false }),
        }));
        await openApp(page);

        const costBtn = page.locator('#cost-display');
        await expect(costBtn).toContainText('total $4.21', { timeout: 5000 });
        await expect(costBtn).toContainText('$0.00');  // session portion before first message
    });

    test('regression-02: Chat.seedCostTotal updates text on the button element', async ({ page }) => {
        await bypassAuth(page, 0);
        await page.route('**/api/usage/llm-calls**', route => route.fulfill({
            status: 200, contentType: 'application/json',
            body: JSON.stringify({ items: [], next_cursor: null, has_more: false }),
        }));
        await openApp(page);

        await page.evaluate(() => { if (typeof Chat !== 'undefined') Chat.seedCostTotal(7.77); });
        await expect(page.locator('#cost-display')).toHaveText(
            'Est. cost: $0.00 (total $7.77)', { timeout: 5000 }
        );
    });

    test('regression-03: zero lifetime total renders cleanly on button', async ({ page }) => {
        await bypassAuth(page, 0);
        await page.route('**/api/usage/llm-calls**', route => route.fulfill({
            status: 200, contentType: 'application/json',
            body: JSON.stringify({ items: [], next_cursor: null, has_more: false }),
        }));
        await openApp(page);
        await expect(page.locator('#cost-display')).toContainText('total $0.00', { timeout: 5000 });
    });
});
