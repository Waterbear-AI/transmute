// @ts-check
/**
 * E2E spec for FE-002: Auto-switch to Profile tab on profile.snapshot SSE event.
 * Verifies that handleSSEEvent('profile.snapshot', data) sets profile_snapshots,
 * calls _renderTabs() (making the tab visible), then _switchTab('profile')
 * (activating it) — in that exact order — without JS errors.
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

const PROFILE_SSE_PAYLOAD = {
    quadrant: 'The Magnifier',
    interpretation: 'You amplify what you receive, reflecting patterns back with clarity.',
    structured_insights: {
        strengths: [{ dimension: 'Meta-Cognitive Awareness', level: 'Strong', score: 3.38, note: null }],
        growth_areas: [],
        cross_dimensional_insights: []
    },
    scores: {
        'Meta-Cognitive Awareness': { weighted_avg: 3.38, sub_dimensions: {} }
    }
};

test.describe('Profile Tab Auto-Switch (FE-002)', () => {

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

    test('auto-switch-01: profile.snapshot SSE switches active tab to Profile', async ({ page }) => {
        // Initially on orientation tab — Profile tab should not be visible
        const profileTabBefore = page.locator('.results-tab', { hasText: 'Profile' });
        await expect(profileTabBefore).toHaveCount(0);

        // Simulate the profile.snapshot SSE event via handleSSEEvent
        await page.evaluate((data) => {
            if (typeof Results !== 'undefined') {
                Results.handleSSEEvent('profile.snapshot', data);
            }
        }, PROFILE_SSE_PAYLOAD);
        await page.waitForTimeout(300);

        // Profile tab must now be visible
        const profileTab = page.locator('.results-tab', { hasText: 'Profile' });
        await expect(profileTab).toBeVisible({ timeout: 5000 });

        // Profile tab must be the active (selected) tab
        await expect(profileTab).toHaveClass(/results-tab--active/);

        // Profile tab content must be rendered
        const content = page.locator('#results-content');
        await expect(content).toContainText('Your Profile');
        await expect(content).toContainText('The Magnifier');

        await page.screenshot({
            path: `${SCREENSHOTS_DIR}/auto-switch-01-profile-active.png`,
            fullPage: false
        });
    });

    test('auto-switch-02: Profile tab is visible before switch (not hidden when switched)', async ({ page }) => {
        // Simulate SSE event
        await page.evaluate((data) => {
            if (typeof Results !== 'undefined') {
                Results.handleSSEEvent('profile.snapshot', data);
            }
        }, PROFILE_SSE_PAYLOAD);
        await page.waitForTimeout(300);

        // Verify the tab button has aria-selected="true"
        const profileTab = page.locator('[role="tab"]', { hasText: 'Profile' });
        await expect(profileTab).toBeVisible({ timeout: 5000 });
        await expect(profileTab).toHaveAttribute('aria-selected', 'true');

        // Content area must be populated (not blank), confirming render happened after tab switch
        const content = page.locator('#results-content');
        const contentText = await content.textContent();
        expect(contentText.trim().length).toBeGreaterThan(0);
    });

    test('auto-switch-03: 0 JS errors during auto-switch', async ({ page }) => {
        // Fire SSE event
        await page.evaluate((data) => {
            if (typeof Results !== 'undefined') {
                Results.handleSSEEvent('profile.snapshot', data);
            }
        }, PROFILE_SSE_PAYLOAD);
        await page.waitForTimeout(400);

        // afterEach hook asserts 0 pageerror events — this test just confirms the flow completes
        const profileTab = page.locator('.results-tab--active', { hasText: 'Profile' });
        await expect(profileTab).toBeVisible({ timeout: 5000 });
    });
});
