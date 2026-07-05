// @ts-check
/**
 * E2E spec for FE-001: Results.applyEarlyResult(earlyResult) public method.
 *
 * Verifies the NEW public API (spec.md B6.6 / Required Implementation):
 *  - stores earlyResult into _resultsData.assessment_state.early_result
 *    (mirroring the assessment.transmute_result SSE handler) so it survives
 *    a tab switch just like the SSE-driven path (B6.2)
 *  - re-renders the early-result card immediately when the Assessment tab
 *    is already active
 *  - does NOT force a tab switch or throw when the Assessment tab is NOT
 *    active -- it stores durably and renders lazily on the next visit
 *  - a null/falsy earlyResult is a no-op (does not clobber existing state)
 *  - zero uncaught JS errors, matching early-result.spec.js's established
 *    pattern for this module
 */
const { test, expect } = require('@playwright/test');
const path = require('path');

const SCREENSHOTS_DIR = path.join(__dirname, 'screenshots');

async function switchToAssessmentTab(page) {
    await page.locator('.results-tab', { hasText: 'Assessment' }).click();
}

async function bypassAuth(page) {
    await page.route('**/auth/me', route => {
        route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify({ user_id: 'test-user', name: 'Test User', email: 'test@example.com' })
        });
    });
    await page.route('**/api/sessions', route => {
        route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify({ sessions: [] })
        });
    });
    await page.route('**/api/results/**', route => {
        route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify({})
        });
    });
}

const PROGRESS_PAYLOAD = {
    progress: {
        answered: 5,
        total: 113,
        assessment_tier: 'transmute_core',
        dimension_progress: {
            'Transmutation Capacity': { answered: 5, total: 8 }
        }
    }
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

test.describe('Results.applyEarlyResult public method (FE-001)', () => {

    test.beforeEach(async ({ page }) => {
        page._jsErrors = [];
        page.on('pageerror', err => page._jsErrors.push(err.message));

        await bypassAuth(page);
        await page.goto('/');
        await page.locator('#app').waitFor({ state: 'visible', timeout: 10000 });
        await page.waitForLoadState('networkidle');
    });

    test.afterEach(async ({ page }) => {
        const jsErrors = page._jsErrors || [];
        if (jsErrors.length > 0) {
            throw new Error(`Uncaught JS errors detected: ${jsErrors.join('; ')}`);
        }
    });

    test('apply-early-01: applyEarlyResult re-renders immediately when Assessment tab is active', async ({ page }) => {
        await page.evaluate((data) => {
            Results.handleSSEEvent('assessment.progress', data);
        }, PROGRESS_PAYLOAD);
        await page.waitForTimeout(200);
        await switchToAssessmentTab(page);

        // No early-result card yet.
        await expect(page.locator('#early-transmute-result')).toHaveCount(0);

        await page.evaluate((data) => {
            Results.applyEarlyResult(data);
        }, EARLY_RESULT_PAYLOAD);
        await page.waitForTimeout(200);

        const card = page.locator('#early-transmute-result');
        await expect(card).toBeVisible({ timeout: 5000 });
        await expect(card).toContainText('Transmuter');

        // Progress-bar fields from the earlier event must survive the merge.
        const overall = page.locator('#assessment-progress-overall');
        await expect(overall).toContainText('5 / 113');

        await page.screenshot({
            path: `${SCREENSHOTS_DIR}/apply-early-01-active-tab-rerenders.png`,
            fullPage: false
        });
    });

    test('apply-early-02: applyEarlyResult stores durably and does not force a tab switch when Assessment is inactive', async ({ page }) => {
        // Stay on Orientation (default active tab) -- do not switch to Assessment.
        await page.evaluate((data) => {
            Results.applyEarlyResult(data);
        }, EARLY_RESULT_PAYLOAD);
        await page.waitForTimeout(200);

        // Still on Orientation -- no forced navigation, no card rendered into
        // a tab that isn't showing.
        await expect(page.locator('#early-transmute-result')).toHaveCount(0);

        // Now switch to Assessment -- the stored value renders lazily (B6.2:
        // redraw-from-stored-state, same mechanism as the SSE path surviving
        // a tab switch).
        await switchToAssessmentTab(page);
        await page.waitForTimeout(200);

        const card = page.locator('#early-transmute-result');
        await expect(card).toBeVisible({ timeout: 5000 });
        await expect(card).toContainText('Transmuter');
    });

    test('apply-early-03: applyEarlyResult survives a subsequent tab switch away and back', async ({ page }) => {
        // applyEarlyResult itself makes the Assessment tab appear (it stores
        // into assessment_state, which _isTabVisible gates on) -- no need to
        // seed progress first.
        await page.evaluate((data) => {
            Results.applyEarlyResult(data);
        }, EARLY_RESULT_PAYLOAD);
        await switchToAssessmentTab(page);
        await page.waitForTimeout(200);
        await expect(page.locator('#early-transmute-result')).toBeVisible({ timeout: 5000 });

        const orientationTab = page.locator('.results-tab', { hasText: 'Orientation' });
        await orientationTab.click();
        await page.waitForTimeout(200);
        await expect(page.locator('#early-transmute-result')).toHaveCount(0);

        await switchToAssessmentTab(page);
        await page.waitForTimeout(200);

        const card = page.locator('#early-transmute-result');
        await expect(card).toBeVisible({ timeout: 5000 });
        await expect(card).toContainText('Transmuter');

        await page.screenshot({
            path: `${SCREENSHOTS_DIR}/apply-early-03-survives-tab-switch.png`,
            fullPage: false
        });
    });

    test('apply-early-04: a falsy earlyResult is a no-op and does not clobber existing state', async ({ page }) => {
        await page.evaluate((data) => {
            Results.applyEarlyResult(data);
        }, EARLY_RESULT_PAYLOAD);
        await switchToAssessmentTab(page);
        await page.waitForTimeout(200);
        await expect(page.locator('#early-transmute-result')).toContainText('Transmuter');

        // Calling with null must not clear the existing card.
        await page.evaluate(() => {
            Results.applyEarlyResult(null);
        });
        await page.waitForTimeout(200);
        await expect(page.locator('#early-transmute-result')).toBeVisible();
        await expect(page.locator('#early-transmute-result')).toContainText('Transmuter');
    });

    test('apply-early-05: 0 JS errors across the full applyEarlyResult + tab-switch flow', async ({ page }) => {
        await page.evaluate((data) => {
            Results.handleSSEEvent('assessment.progress', data);
        }, PROGRESS_PAYLOAD);
        await switchToAssessmentTab(page);
        await page.evaluate((data) => {
            Results.applyEarlyResult(data);
        }, EARLY_RESULT_PAYLOAD);
        await page.waitForTimeout(300);

        const orientationTab = page.locator('.results-tab', { hasText: 'Orientation' });
        await orientationTab.click();
        await page.waitForTimeout(200);
        await switchToAssessmentTab(page);
        await page.waitForTimeout(200);

        // afterEach asserts 0 pageerror events -- this test just exercises the flow.
        await expect(page.locator('#early-transmute-result')).toBeVisible({ timeout: 5000 });
    });
});
