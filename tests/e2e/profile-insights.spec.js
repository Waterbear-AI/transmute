// @ts-check
/**
 * E2E spec for FE-001: Structured profile insight sections.
 * Tests rendering of Top Strengths, Growth Areas, Cross-Dimensional Insights,
 * and sub-dimension progress bars. Also verifies graceful fallback for legacy
 * snapshots that lack structured_insights.
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

/** Full profile snapshot with structured_insights */
const PROFILE_WITH_INSIGHTS = {
    quadrant: 'The Magnifier',
    interpretation: 'You amplify what you receive, reflecting patterns back with clarity.',
    structured_insights: {
        strengths: [
            {
                dimension: 'Meta-Cognitive Awareness',
                level: 'Strong',
                score: 3.38,
                note: 'Excellent self-monitoring of thought patterns.'
            },
            {
                dimension: 'Temporal Awareness',
                level: 'Strong',
                score: 3.47,
                note: null
            }
        ],
        growth_areas: [
            {
                dimension: 'Emotional Awareness',
                level: 'Developing',
                score: 2.79,
                note: 'Room to grow in recognizing emotional triggers.'
            }
        ],
        cross_dimensional_insights: [
            'You see downstream effects but miss early emotional triggers.',
            'Strong pattern recognition compensates for reactive tendencies.'
        ]
    },
    scores: {
        'Meta-Cognitive Awareness': {
            weighted_avg: 3.38,
            sub_dimensions: {
                'Bias Detection': { score: 3.67, answered: 5 },
                'Self-Reflection': { score: 3.50, answered: 5 }
            }
        },
        'Temporal Awareness': {
            weighted_avg: 3.47,
            sub_dimensions: {
                'Future Projection': { score: 3.67, answered: 4 }
            }
        },
        'Emotional Awareness': {
            weighted_avg: 2.79,
            sub_dimensions: {
                'Trigger Awareness': { score: 1.67, answered: 3 },
                'Emotional Regulation': { score: 3.50, answered: 5 }
            }
        }
    }
};

/** Legacy snapshot — no structured_insights */
const PROFILE_LEGACY = {
    quadrant: 'The Conduit',
    interpretation: 'You channel information efficiently across contexts.',
    scores: {
        'Metacognition': { weighted_avg: 2.9, sub_dimensions: {} }
    }
};

async function showProfileTab(page, profileData) {
    await page.evaluate((data) => {
        if (typeof Results !== 'undefined') {
            Results.update({ latest_profile: data }, 'profile');
        }
    }, profileData);
    await page.waitForTimeout(300);
}

test.describe('Profile Insight Sections (FE-001)', () => {

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

    test('profile-01: Top Strengths section renders with dimension and level', async ({ page }) => {
        await showProfileTab(page, PROFILE_WITH_INSIGHTS);

        const strengthsSection = page.locator('.profile-insight-section').filter({ hasText: 'Top Strengths' });
        await expect(strengthsSection).toBeVisible({ timeout: 5000 });
        await expect(strengthsSection).toContainText('Meta-Cognitive Awareness');
        await expect(strengthsSection).toContainText('Strong');
        await expect(strengthsSection).toContainText('3.38');
        await expect(strengthsSection).toContainText('Temporal Awareness');

        await page.screenshot({
            path: `${SCREENSHOTS_DIR}/profile-01-strengths.png`,
            fullPage: false
        });
    });

    test('profile-02: Growth Areas section renders correctly', async ({ page }) => {
        await showProfileTab(page, PROFILE_WITH_INSIGHTS);

        const growthSection = page.locator('.profile-insight-section').filter({ hasText: 'Growth Areas' });
        await expect(growthSection).toBeVisible({ timeout: 5000 });
        await expect(growthSection).toContainText('Emotional Awareness');
        await expect(growthSection).toContainText('Developing');
        await expect(growthSection).toContainText('2.79');

        await page.screenshot({
            path: `${SCREENSHOTS_DIR}/profile-02-growth-areas.png`,
            fullPage: false
        });
    });

    test('profile-03: Cross-Dimensional Insights section renders', async ({ page }) => {
        await showProfileTab(page, PROFILE_WITH_INSIGHTS);

        const crossSection = page.locator('.profile-insight-section').filter({ hasText: 'Cross-Dimensional Insights' });
        await expect(crossSection).toBeVisible({ timeout: 5000 });
        await expect(crossSection).toContainText('You see downstream effects but miss early emotional triggers.');
        await expect(crossSection).toContainText('Strong pattern recognition compensates for reactive tendencies.');

        await page.screenshot({
            path: `${SCREENSHOTS_DIR}/profile-03-cross-dimensional.png`,
            fullPage: false
        });
    });

    test('profile-04: Sub-dimension progress bars render under strengths', async ({ page }) => {
        await showProfileTab(page, PROFILE_WITH_INSIGHTS);

        // Sub-dimension rows should appear under the strengths section
        const strengthsSection = page.locator('.profile-insight-section').filter({ hasText: 'Top Strengths' });
        const subdimRows = strengthsSection.locator('.profile-subdim__row');
        const count = await subdimRows.count();
        expect(count).toBeGreaterThan(0);

        // Check that sub-dim labels appear
        await expect(strengthsSection).toContainText('Bias Detection');
        await expect(strengthsSection).toContainText('Self-Reflection');

        // Verify progress bars exist within the sub-dim rows
        const firstSubDimBar = subdimRows.first().locator('.progress-bar');
        await expect(firstSubDimBar).toBeVisible();

        await page.screenshot({
            path: `${SCREENSHOTS_DIR}/profile-04-subdim-bars.png`,
            fullPage: false
        });
    });

    test('profile-05: XSS text is inert — HTML tags rendered as text', async ({ page }) => {
        const xssProfile = {
            ...PROFILE_WITH_INSIGHTS,
            structured_insights: {
                strengths: [
                    {
                        dimension: '<script>alert("xss")</script>Awareness',
                        level: 'Strong',
                        score: 3.5,
                        note: '<img src=x onerror=alert(1)>'
                    }
                ],
                growth_areas: [],
                cross_dimensional_insights: ['<b>bold insight</b>']
            }
        };

        await showProfileTab(page, xssProfile);

        // Script tag should NOT have been executed — check no alert dialog appeared
        // (Playwright throws on unexpected dialogs by default if we attach a handler)
        const strengthsSection = page.locator('.profile-insight-section').filter({ hasText: 'Top Strengths' });
        await expect(strengthsSection).toBeVisible({ timeout: 5000 });

        // The raw HTML tag text should appear as text content, not parsed HTML
        const rawHtml = await page.evaluate(() => {
            const items = document.querySelectorAll('.profile-insight__item');
            return Array.from(items).map(el => el.textContent).join(' ');
        });
        // The <script> should appear as literal text, not as an executed script
        expect(rawHtml).toContain('<script>');
        // No <b> element should exist inside insight items (XSS via innerHTML)
        const boldCount = await page.locator('.profile-insight__item b').count();
        expect(boldCount).toBe(0);
    });

    test('profile-06: Legacy snapshot without structured_insights renders gracefully', async ({ page }) => {
        await showProfileTab(page, PROFILE_LEGACY);

        // Profile tab should render (no crash)
        const content = page.locator('#results-content');
        await expect(content).toBeVisible({ timeout: 5000 });

        // Should show interpretation fallback
        await expect(content).toContainText('You channel information efficiently across contexts.');

        // No insight sections should appear
        const sectionCount = await page.locator('.profile-insight-section').count();
        expect(sectionCount).toBe(0);

        await page.screenshot({
            path: `${SCREENSHOTS_DIR}/profile-06-legacy-fallback.png`,
            fullPage: false
        });
    });

    test('profile-07: Dimension Scores section renders with sub-dimension bars', async ({ page }) => {
        await showProfileTab(page, PROFILE_WITH_INSIGHTS);

        const content = page.locator('#results-content');
        // The "Dimension Scores" header should be present
        await expect(content).toContainText('Dimension Scores');

        // Sub-dimension rows in the Dimension Scores section
        const subdimRows = content.locator('.profile-subdim__row');
        const count = await subdimRows.count();
        expect(count).toBeGreaterThan(0);

        await page.screenshot({
            path: `${SCREENSHOTS_DIR}/profile-07-dim-scores.png`,
            fullPage: false
        });
    });
});
