// @ts-check
/**
 * Screenshot spec for the new regression panel states.
 * Captures all four rendering states for BE-001 visual verification.
 */
const { test, expect } = require('@playwright/test');
const path = require('path');

const SCREENSHOTS_DIR = path.join(__dirname, 'screenshots');

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

async function showReassessmentTab(page, checkInsData) {
    await page.evaluate((data) => {
        if (typeof Results !== 'undefined') {
            Results.update({ check_ins: data }, 'reassessment');
        }
    }, checkInsData);
    // Wait for tab content to render
    await page.waitForTimeout(300);
}

test.describe('Regression Panel Visual Verification', () => {

    test.beforeEach(async ({ page }) => {
        // Register pageerror listener to catch uncaught JS exceptions
        page._jsErrors = [];
        page.on('pageerror', err => page._jsErrors.push(err.message));

        await bypassAuth(page);
        await page.goto('/');
        await page.locator('#app').waitFor({ state: 'visible', timeout: 10000 });
        await page.waitForLoadState('networkidle');
    });

    test.afterEach(async ({ page }) => {
        // Assert 0 JS errors across all tests
        const jsErrors = page._jsErrors || [];
        if (jsErrors.length > 0) {
            throw new Error(`Uncaught JS errors detected: ${jsErrors.join('; ')}`);
        }
    });

    test('regression-01-detected state renders correctly', async ({ page }) => {
        await showReassessmentTab(page, {
            count: 1,
            latest_regression: true,
            latest_created_at: new Date().toISOString(),
            latest_regression_detail: {
                evaluated: true,
                regression_detected: true,
                reason: 'Regression detected: 2 dimension(s) dropped > 15 pts on the 0-100 scale',
                threshold_normalized: 15.0,
                regressed_dimensions: [
                    { dimension: 'Emotional Regulation', baseline_normalized: 78.0, current_normalized: 55.5, drop_normalized: 22.5 },
                    { dimension: 'Self-Compassion', baseline_normalized: 70.1, current_normalized: 51.8, drop_normalized: 18.3 }
                ],
                quadrant: { baseline: 'Transmuter', current: 'Conduit', downgraded: true },
                baseline_snapshot_id: 'snap-baseline-001',
                check_in_snapshot_id: 'snap-checkin-001'
            },
            latest_comparison: null
        });

        const panel = page.locator('.regression-panel').first();
        await expect(panel).toBeVisible({ timeout: 5000 });
        await expect(panel).toContainText('Regression Detail');
        await expect(panel).toContainText('Emotional Regulation');
        await expect(panel).toContainText('Quadrant: Transmuter ▾ Conduit (downgraded)');

        await page.screenshot({
            path: `${SCREENSHOTS_DIR}/regression-01-detected.png`,
            fullPage: false
        });
    });

    test('regression-02-clean state renders correctly', async ({ page }) => {
        await showReassessmentTab(page, {
            count: 1,
            latest_regression: false,
            latest_created_at: new Date().toISOString(),
            latest_regression_detail: {
                evaluated: true,
                regression_detected: false,
                reason: 'No regression detected: all dimensions within threshold',
                threshold_normalized: 15.0,
                regressed_dimensions: [],
                quadrant: { baseline: 'Transmuter', current: 'Transmuter', downgraded: false },
                baseline_snapshot_id: 'snap-baseline-001',
                check_in_snapshot_id: 'snap-checkin-002'
            },
            latest_comparison: null
        });

        const panel = page.locator('.regression-panel--clean').first();
        await expect(panel).toBeVisible({ timeout: 5000 });
        await expect(panel).toContainText('No regression detected');

        await page.screenshot({
            path: `${SCREENSHOTS_DIR}/regression-02-clean.png`,
            fullPage: false
        });
    });

    test('regression-03-unavailable state renders correctly', async ({ page }) => {
        await showReassessmentTab(page, {
            count: 1,
            latest_regression: null,
            latest_created_at: new Date().toISOString(),
            latest_regression_detail: {
                evaluated: false,
                regression_detected: false,
                reason: 'No graduation baseline found for user',
                threshold_normalized: 15.0,
                regressed_dimensions: [],
                quadrant: { baseline: '', current: '', downgraded: false }
            },
            latest_comparison: null
        });

        const panel = page.locator('.regression-panel--unavailable').first();
        await expect(panel).toBeVisible({ timeout: 5000 });
        await expect(panel).toContainText('Regression comparison unavailable');

        await page.screenshot({
            path: `${SCREENSHOTS_DIR}/regression-03-unavailable.png`,
            fullPage: false
        });
    });

    test('regression-04-no-detail no-op guard works', async ({ page }) => {
        // No latest_regression_detail — should not render a regression panel
        await showReassessmentTab(page, {
            count: 1,
            latest_regression: false,
            latest_created_at: new Date().toISOString(),
            latest_regression_detail: null,
            latest_comparison: null
        });

        const panelCount = await page.locator('.regression-panel').count();
        expect(panelCount).toBe(0);

        await page.screenshot({
            path: `${SCREENSHOTS_DIR}/regression-04-no-detail.png`,
            fullPage: false
        });
    });
});
