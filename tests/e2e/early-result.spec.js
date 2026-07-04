// @ts-check
/**
 * E2E spec for FE-001: Early transmute result rendering (Tier-1 completion).
 *
 * Verifies that Results.handleSSEEvent('assessment.transmute_result', data):
 *  - merges the payload into _resultsData.assessment_state.early_result
 *    without clobbering existing progress-bar fields (answered/total/
 *    dimension_progress)
 *  - renders the early-result card (#early-transmute-result) with an honest
 *    confidence band, a description, and a QuadrantChart
 *  - the card survives a tab switch away and back (re-drawn from stored
 *    state, not held only in a live DOM node) — spec.md B6.2
 *  - the tier-progress affordance reflects assessment_tier
 *  - zero uncaught JS errors throughout (page.on('pageerror') + throw-on-error
 *    in afterEach, matching profile-auto-switch.spec.js's established pattern)
 */
const { test, expect } = require('@playwright/test');
const path = require('path');

const SCREENSHOTS_DIR = path.join(__dirname, 'screenshots');

/**
 * Click the Assessment results-tab. assessment.progress/assessment.transmute_result
 * deliberately do NOT auto-switch tabs (unlike profile.snapshot) — a Tier-1
 * completion arriving mid-chat shouldn't yank the user's Results-panel view
 * away from wherever they're looking. Tests must switch to Assessment
 * explicitly to observe its rendered content, matching real user behavior.
 */
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

const TRANSMUTE_RESULT_PAYLOAD = {
    event_type: 'assessment.transmute_result',
    archetype: 'magnifier',
    x: 0.42,
    y: -0.18,
    confidence: 'medium',
    confidence_reason: 'Based on ~18 core answers; a few more will sharpen it.'
};

test.describe('Early Transmute Result Card (FE-001)', () => {

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

    test('early-01: assessment.transmute_result renders the early-result card', async ({ page }) => {
        // Seed progress first (as the real flow does — progress events precede
        // the Tier-1 completion event).
        await page.evaluate((data) => {
            Results.handleSSEEvent('assessment.progress', data);
        }, PROGRESS_PAYLOAD);
        await page.waitForTimeout(200);

        await page.evaluate((data) => {
            Results.handleSSEEvent('assessment.transmute_result', data);
        }, TRANSMUTE_RESULT_PAYLOAD);
        await page.waitForTimeout(300);
        await switchToAssessmentTab(page);

        const card = page.locator('#early-transmute-result');
        await expect(card).toBeVisible({ timeout: 5000 });
        await expect(card).toContainText('Early Transmute Read');
        await expect(card).toContainText('Magnifier');

        // Confidence band present with honest plain-language reason.
        const band = card.locator('.confidence-band');
        await expect(band).toBeVisible();
        await expect(band).toContainText('Medium confidence');
        await expect(band).toContainText('a few more will sharpen it');

        // Progress bar data from the earlier event must NOT be clobbered.
        const overall = page.locator('#assessment-progress-overall');
        await expect(overall).toContainText('5 / 113');

        await page.screenshot({
            path: `${SCREENSHOTS_DIR}/early-01-card-rendered.png`,
            fullPage: false
        });
    });

    test('early-02: card survives switching away to another tab and back', async ({ page }) => {
        await page.evaluate((data) => {
            Results.handleSSEEvent('assessment.progress', data);
        }, PROGRESS_PAYLOAD);
        await page.evaluate((data) => {
            Results.handleSSEEvent('assessment.transmute_result', data);
        }, TRANSMUTE_RESULT_PAYLOAD);
        await page.waitForTimeout(300);
        await switchToAssessmentTab(page);

        await expect(page.locator('#early-transmute-result')).toBeVisible({ timeout: 5000 });

        // Switch to Orientation tab (always present) and back to Assessment.
        const orientationTab = page.locator('.results-tab', { hasText: 'Orientation' });
        await orientationTab.click();
        await page.waitForTimeout(200);
        await expect(page.locator('#early-transmute-result')).toHaveCount(0);

        await switchToAssessmentTab(page);
        await page.waitForTimeout(200);

        // Re-drawn from stored state, not a live-only DOM node.
        const card = page.locator('#early-transmute-result');
        await expect(card).toBeVisible({ timeout: 5000 });
        await expect(card).toContainText('Magnifier');

        await page.screenshot({
            path: `${SCREENSHOTS_DIR}/early-02-survives-tab-switch.png`,
            fullPage: false
        });
    });

    test('early-03: tier-progress affordance reflects assessment_tier', async ({ page }) => {
        await page.evaluate((data) => {
            Results.handleSSEEvent('assessment.progress', data);
        }, PROGRESS_PAYLOAD);
        await page.waitForTimeout(300);
        await switchToAssessmentTab(page);

        const tier = page.locator('.tier-progress');
        await expect(tier).toBeVisible({ timeout: 5000 });
        await expect(tier).toContainText('Tier 1 of 3');
    });

    test('early-04: low confidence renders an honest low-confidence badge (no false certainty)', async ({ page }) => {
        const lowPayload = Object.assign({}, TRANSMUTE_RESULT_PAYLOAD, {
            archetype: 'conduit',
            confidence: 'low',
            confidence_reason: 'Only a few answers so far — this is a rough early guess.'
        });
        await page.evaluate((data) => {
            Results.handleSSEEvent('assessment.transmute_result', data);
        }, lowPayload);
        await page.waitForTimeout(300);
        await switchToAssessmentTab(page);

        const band = page.locator('.confidence-band');
        await expect(band).toBeVisible({ timeout: 5000 });
        await expect(band).toContainText('Low confidence');
        await expect(band).toContainText('rough early guess');
    });

    test('early-05: 0 JS errors across the full render + tab-switch flow', async ({ page }) => {
        await page.evaluate((data) => {
            Results.handleSSEEvent('assessment.progress', data);
        }, PROGRESS_PAYLOAD);
        await page.evaluate((data) => {
            Results.handleSSEEvent('assessment.transmute_result', data);
        }, TRANSMUTE_RESULT_PAYLOAD);
        await page.waitForTimeout(300);
        await switchToAssessmentTab(page);

        const orientationTab = page.locator('.results-tab', { hasText: 'Orientation' });
        await orientationTab.click();
        await page.waitForTimeout(200);
        await switchToAssessmentTab(page);
        await page.waitForTimeout(200);

        // afterEach asserts 0 pageerror events — this test just exercises the flow.
        await expect(page.locator('#early-transmute-result')).toBeVisible({ timeout: 5000 });
    });
});
