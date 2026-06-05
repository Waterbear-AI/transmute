// @ts-check
/**
 * Comprehensive E2E test suite for the Profile tab structured insights feature.
 * Covers: Top Strengths / Growth Areas / Cross-Dimensional Insights rendering,
 * sub-dimension progress bars, profile.snapshot SSE auto-switch, graceful fallback
 * for legacy snapshots (no structured_insights), and XSS protection for LLM text.
 *
 * TEST-001: Profile Tab Structured Insights E2E
 */
const { test, expect } = require('@playwright/test');
const path = require('path');

const SCREENSHOTS_DIR = path.join(__dirname, 'screenshots');

// ─── Auth / API Mocks ───────────────────────────────────────────────────────

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

// ─── Test Fixtures ──────────────────────────────────────────────────────────

/** Full profile snapshot with structured_insights */
const FULL_PROFILE = {
    quadrant: 'The Magnifier',
    interpretation: 'You amplify what you receive, reflecting patterns back with clarity.',
    structured_insights: {
        strengths: [
            { dimension: 'Meta-Cognitive Awareness', level: 'Strong', score: 3.38, note: 'Excellent self-monitoring.' },
            { dimension: 'Temporal Awareness', level: 'Strong', score: 3.47, note: null }
        ],
        growth_areas: [
            { dimension: 'Emotional Awareness', level: 'Developing', score: 2.79, note: 'Room to grow in recognizing emotional triggers.' }
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

/** Legacy snapshot — no structured_insights field */
const LEGACY_PROFILE = {
    quadrant: 'The Conduit',
    interpretation: 'You channel information efficiently across contexts.',
    scores: {
        'Metacognition': { weighted_avg: 2.9, sub_dimensions: {} }
    }
};

/** Profile with XSS payloads in LLM-authored fields */
const XSS_PROFILE = {
    quadrant: 'The Magnifier',
    interpretation: '<script>alert("xss-interp")</script>Normal text.',
    structured_insights: {
        strengths: [
            {
                dimension: '<script>alert("xss-dim")</script>Awareness',
                level: 'Strong',
                score: 3.5,
                note: '<img src=x onerror=alert(1)>'
            }
        ],
        growth_areas: [],
        cross_dimensional_insights: ['<b>bold insight</b> <script>alert(2)</script>']
    },
    scores: {}
};

// ─── Helpers ────────────────────────────────────────────────────────────────

/** Load the app and bypass auth */
async function loadApp(page) {
    await bypassAuth(page);
    await page.goto('/');
    await page.locator('#app').waitFor({ state: 'visible', timeout: 10000 });
    await page.waitForLoadState('networkidle');
}

/** Trigger the Profile tab via Results.update (simulates API data load) */
async function showProfileViaUpdate(page, profileData) {
    await page.evaluate((data) => {
        if (typeof Results !== 'undefined') {
            Results.update({ latest_profile: data }, 'profile');
        }
    }, profileData);
    await page.waitForTimeout(300);
}

/** Trigger the Profile tab via handleSSEEvent (simulates live SSE) */
async function showProfileViaSSE(page, profileData) {
    await page.evaluate((data) => {
        if (typeof Results !== 'undefined') {
            Results.handleSSEEvent('profile.snapshot', data);
        }
    }, profileData);
    await page.waitForTimeout(300);
}

// ─── Test Suite ─────────────────────────────────────────────────────────────

test.describe('Profile Tab — Structured Insights (TEST-001)', () => {

    test.beforeEach(async ({ page }) => {
        // Register JS error listener — afterEach asserts 0 errors
        page._jsErrors = [];
        page.on('pageerror', err => page._jsErrors.push(err.message));
        await loadApp(page);
    });

    test.afterEach(async ({ page }) => {
        const jsErrors = page._jsErrors || [];
        if (jsErrors.length > 0) {
            throw new Error(`Uncaught JS errors: ${jsErrors.join('; ')}`);
        }
    });

    // ── Structured Sections Rendering ─────────────────────────────────────

    test('profile-tab-01: Top Strengths section renders with dimension, level, score', async ({ page }) => {
        await showProfileViaUpdate(page, FULL_PROFILE);

        const section = page.locator('.profile-insight-section').filter({ hasText: 'Top Strengths' });
        await expect(section).toBeVisible({ timeout: 5000 });
        await expect(section).toContainText('Meta-Cognitive Awareness');
        await expect(section).toContainText('Strong');
        await expect(section).toContainText('3.38');
        await expect(section).toContainText('Temporal Awareness');
        await expect(section).toContainText('3.47');
        // Note field visible for first item
        await expect(section).toContainText('Excellent self-monitoring.');

        await page.screenshot({ path: `${SCREENSHOTS_DIR}/profile-tab-01-strengths.png` });
    });

    test('profile-tab-02: Growth Areas section renders with dimension, level, score, note', async ({ page }) => {
        await showProfileViaUpdate(page, FULL_PROFILE);

        const section = page.locator('.profile-insight-section').filter({ hasText: 'Growth Areas' });
        await expect(section).toBeVisible({ timeout: 5000 });
        await expect(section).toContainText('Emotional Awareness');
        await expect(section).toContainText('Developing');
        await expect(section).toContainText('2.79');
        await expect(section).toContainText('Room to grow in recognizing emotional triggers.');

        await page.screenshot({ path: `${SCREENSHOTS_DIR}/profile-tab-02-growth-areas.png` });
    });

    test('profile-tab-03: Cross-Dimensional Insights section renders all insights', async ({ page }) => {
        await showProfileViaUpdate(page, FULL_PROFILE);

        const section = page.locator('.profile-insight-section').filter({ hasText: 'Cross-Dimensional Insights' });
        await expect(section).toBeVisible({ timeout: 5000 });
        await expect(section).toContainText('You see downstream effects but miss early emotional triggers.');
        await expect(section).toContainText('Strong pattern recognition compensates for reactive tendencies.');

        await page.screenshot({ path: `${SCREENSHOTS_DIR}/profile-tab-03-cross-dimensional.png` });
    });

    test('profile-tab-04: All three insight sections present simultaneously', async ({ page }) => {
        await showProfileViaUpdate(page, FULL_PROFILE);

        const sections = page.locator('.profile-insight-section');
        await expect(sections).toHaveCount(3, { timeout: 5000 });
    });

    // ── Sub-Dimension Breakdown Rendering ─────────────────────────────────

    test('profile-tab-05: Sub-dimension bars appear under Strengths items', async ({ page }) => {
        await showProfileViaUpdate(page, FULL_PROFILE);

        const strengthsSection = page.locator('.profile-insight-section').filter({ hasText: 'Top Strengths' });
        await expect(strengthsSection).toBeVisible({ timeout: 5000 });

        // Sub-dim rows should exist within the strengths section
        const subdimRows = strengthsSection.locator('.profile-subdim__row');
        const count = await subdimRows.count();
        expect(count).toBeGreaterThan(0);

        // Specific sub-dimension names visible
        await expect(strengthsSection).toContainText('Bias Detection');
        await expect(strengthsSection).toContainText('Self-Reflection');
        await expect(strengthsSection).toContainText('Future Projection');

        // Each sub-dim row contains a progress bar
        const firstBar = subdimRows.first().locator('.progress-bar');
        await expect(firstBar).toBeVisible();

        await page.screenshot({ path: `${SCREENSHOTS_DIR}/profile-tab-05-subdim-bars.png` });
    });

    test('profile-tab-06: Sub-dimension bars appear in Dimension Scores section', async ({ page }) => {
        await showProfileViaUpdate(page, FULL_PROFILE);

        const content = page.locator('#results-content');
        await expect(content).toContainText('Dimension Scores', { timeout: 5000 });

        const subdimRows = content.locator('.profile-subdim__row');
        const count = await subdimRows.count();
        expect(count).toBeGreaterThan(0);

        // Accessibility: aria-label on progress bars
        const firstSubdimBar = subdimRows.first().locator('.progress-bar');
        await expect(firstSubdimBar).toBeVisible();

        await page.screenshot({ path: `${SCREENSHOTS_DIR}/profile-tab-06-dim-scores-subdim.png` });
    });

    test('profile-tab-07: Sub-dim bars have aria-label for accessibility', async ({ page }) => {
        await showProfileViaUpdate(page, FULL_PROFILE);

        // Wait for content
        await expect(page.locator('#results-content')).toContainText('Dimension Scores', { timeout: 5000 });

        // Check aria-labels on sub-dimension bars
        const ariaLabelCount = await page.evaluate(() => {
            const bars = document.querySelectorAll('.profile-subdim__bar');
            return Array.from(bars).filter(b => b.getAttribute('aria-label')).length;
        });
        expect(ariaLabelCount).toBeGreaterThan(0);
    });

    // ── SSE Auto-Switch ────────────────────────────────────────────────────

    test('profile-tab-08: profile.snapshot SSE event auto-switches to Profile tab', async ({ page }) => {
        // Initially — Profile tab not visible
        await expect(page.locator('.results-tab', { hasText: 'Profile' })).toHaveCount(0);

        // Fire SSE
        await showProfileViaSSE(page, FULL_PROFILE);

        // Tab becomes visible and active
        const profileTab = page.locator('.results-tab', { hasText: 'Profile' });
        await expect(profileTab).toBeVisible({ timeout: 5000 });
        await expect(profileTab).toHaveClass(/results-tab--active/);

        // Content rendered
        const content = page.locator('#results-content');
        await expect(content).toContainText('Your Profile');

        await page.screenshot({ path: `${SCREENSHOTS_DIR}/profile-tab-08-sse-auto-switch.png` });
    });

    test('profile-tab-09: SSE auto-switch produces aria-selected="true" on Profile tab', async ({ page }) => {
        await showProfileViaSSE(page, FULL_PROFILE);

        const profileTab = page.locator('[role="tab"]', { hasText: 'Profile' });
        await expect(profileTab).toBeVisible({ timeout: 5000 });
        await expect(profileTab).toHaveAttribute('aria-selected', 'true');
    });

    test('profile-tab-10: Profile tab content is populated after SSE auto-switch', async ({ page }) => {
        await showProfileViaSSE(page, FULL_PROFILE);

        const content = page.locator('#results-content');
        const text = await content.textContent();
        expect(text.trim().length).toBeGreaterThan(0);
        await expect(content).toContainText('The Magnifier');
    });

    // ── Legacy Snapshot Fallback ───────────────────────────────────────────

    test('profile-tab-11: Legacy snapshot renders interpretation without crash', async ({ page }) => {
        await showProfileViaUpdate(page, LEGACY_PROFILE);

        const content = page.locator('#results-content');
        await expect(content).toBeVisible({ timeout: 5000 });
        // Fallback interpretation text visible
        await expect(content).toContainText('You channel information efficiently across contexts.');

        await page.screenshot({ path: `${SCREENSHOTS_DIR}/profile-tab-11-legacy-fallback.png` });
    });

    test('profile-tab-12: Legacy snapshot shows NO insight sections (no structured_insights)', async ({ page }) => {
        await showProfileViaUpdate(page, LEGACY_PROFILE);

        await expect(page.locator('#results-content')).toBeVisible({ timeout: 5000 });
        const sectionCount = await page.locator('.profile-insight-section').count();
        expect(sectionCount).toBe(0);
    });

    test('profile-tab-13: Legacy snapshot shows quadrant name correctly', async ({ page }) => {
        await showProfileViaUpdate(page, LEGACY_PROFILE);

        const content = page.locator('#results-content');
        await expect(content).toContainText('The Conduit', { timeout: 5000 });
    });

    // ── XSS Protection ─────────────────────────────────────────────────────

    test('profile-tab-14: HTML tags in structured_insights render as inert text', async ({ page }) => {
        await showProfileViaUpdate(page, XSS_PROFILE);

        const strengthsSection = page.locator('.profile-insight-section').filter({ hasText: 'Top Strengths' });
        await expect(strengthsSection).toBeVisible({ timeout: 5000 });

        // The raw <script> tag text should appear as literal text in textContent
        const rawText = await page.evaluate(() => {
            const items = document.querySelectorAll('.profile-insight__item');
            return Array.from(items).map(el => el.textContent).join(' ');
        });
        // <script> must be present as text (not executed)
        expect(rawText).toContain('<script>');
        // <img> tag text present as text
        expect(rawText).toContain('<img');

        await page.screenshot({ path: `${SCREENSHOTS_DIR}/profile-tab-14-xss-inert.png` });
    });

    test('profile-tab-15: No <script> or <b> tags injected into DOM via innerHTML', async ({ page }) => {
        await showProfileViaUpdate(page, XSS_PROFILE);

        // Wait for render
        const strengthsSection = page.locator('.profile-insight-section').filter({ hasText: 'Top Strengths' });
        await expect(strengthsSection).toBeVisible({ timeout: 5000 });

        // No executable script elements in insight items
        const scriptCount = await page.locator('.profile-insight__item script').count();
        expect(scriptCount).toBe(0);

        // No parsed <b> elements (from cross_dimensional_insights XSS payload)
        const boldCount = await page.locator('.profile-insight__item b').count();
        expect(boldCount).toBe(0);

        // No parsed <img> elements inside insight items
        const imgCount = await page.locator('.profile-insight__item img').count();
        expect(imgCount).toBe(0);
    });

    test('profile-tab-16: XSS payload in cross_dimensional_insights renders as text', async ({ page }) => {
        await showProfileViaUpdate(page, XSS_PROFILE);

        // Force growth areas empty so cross_dim renders
        const xssWithCross = {
            ...XSS_PROFILE,
            structured_insights: {
                strengths: [],
                growth_areas: [],
                cross_dimensional_insights: ['<b>bold insight</b> <script>alert(2)</script>']
            }
        };
        await page.evaluate((data) => {
            if (typeof Results !== 'undefined') Results.update({ latest_profile: data }, 'profile');
        }, xssWithCross);
        await page.waitForTimeout(300);

        const crossSection = page.locator('.profile-insight-section').filter({ hasText: 'Cross-Dimensional Insights' });
        await expect(crossSection).toBeVisible({ timeout: 5000 });

        // No <b> elements rendered — raw text only
        const bCount = await crossSection.locator('b').count();
        expect(bCount).toBe(0);

        // Text content contains the literal tag characters
        await expect(crossSection).toContainText('<b>');
    });

    // ── Visual / Layout Verification ───────────────────────────────────────

    test('profile-tab-17: Full profile tab layout screenshot', async ({ page }) => {
        await showProfileViaUpdate(page, FULL_PROFILE);

        // Wait for all sections
        await expect(page.locator('.profile-insight-section')).toHaveCount(3, { timeout: 5000 });

        await page.screenshot({
            path: `${SCREENSHOTS_DIR}/profile-tab-17-full-layout.png`,
            fullPage: false
        });
    });
});
