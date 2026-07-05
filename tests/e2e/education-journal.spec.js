// @ts-check
/**
 * E2E spec for the "What You've Learned" education journal (FE-002).
 *
 * Verifies:
 *   - Education tab visibility: visible in the education phase with zero
 *     captured content (FR-6, the "hidden until first quiz" fix), and visible
 *     when content exists with no education_progress row (BE-002's
 *     regression guard, surfaced through the frontend visibility gate).
 *   - The journal renders a nested Dimension -> Category accordion in
 *     canonical order, showing only captured dimensions/categories.
 *   - Accordion headers are real <button>s, keyboard operable (Enter/Space),
 *     and aria-expanded reflects state.
 *   - The empty state renders when no content has been captured.
 *   - Captured content renders inert on an XSS payload.
 */
const { test, expect } = require('@playwright/test');
const path = require('path');

const SCREENSHOTS_DIR = path.join(__dirname, 'screenshots');

async function bypassAuth(page, currentPhase) {
    await page.route('**/auth/me', route => route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
            user_id: 'test-user', name: 'Test User', email: 'test@example.com',
            current_phase: currentPhase || 'education',
        }),
    }));
    await page.route('**/api/sessions', route => route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ sessions: [], count: 0, user_total_cost_usd: 0 }),
    }));
    await page.route('**/api/results/**', route => route.fulfill({
        status: 200, contentType: 'application/json', body: JSON.stringify({}),
    }));
}

async function gotoApp(page) {
    await page.goto('/');
    await page.locator('#app').waitFor({ state: 'visible', timeout: 10000 });
    await page.waitForLoadState('networkidle');
}

async function updateResults(page, education, currentPhase) {
    await page.evaluate(({ education, currentPhase }) => {
        Results.update({ education }, currentPhase);
    }, { education, currentPhase });
    await page.waitForTimeout(300);
}

const MULTI_DIM_CONTENT = {
    exists: true,
    progress: {},
    summary: {},
    content: {
        // Deliberately out of canonical order in the payload — the journal
        // must still render Mindful Presence before Meta-Cognitive Awareness
        // is wrong; canonical order per EDUCATION_DIMENSION_ORDER is
        // Meta-Cognitive Awareness THEN Mindful Presence.
        'Mindful Presence': {
            what_this_means: 'Mindful Presence means noticing the current moment.',
        },
        'Meta-Cognitive Awareness': {
            your_score: 'Your score reflects how often you notice your own thinking.',
            what_this_means: '**Meta-Cognitive Awareness** is thinking about your thinking.',
        },
    },
};

test.describe('Education journal ("What You\'ve Learned")', () => {
    test.afterEach(async ({ page }) => {
        const errs = page._jsErrors || [];
        if (errs.length > 0) throw new Error('Uncaught JS errors: ' + errs.join('; '));
    });

    // ── Tab visibility ──────────────────────────────────────────────────────

    test('journal-01: Education tab is visible in the education phase with zero captured content', async ({ page }) => {
        page._jsErrors = [];
        page.on('pageerror', err => page._jsErrors.push(err.message));
        await bypassAuth(page, 'education');
        await gotoApp(page);

        const eduTab = page.locator('.results-tab', { hasText: 'Education' });
        await expect(eduTab).toBeVisible({ timeout: 5000 });
    });

    test('journal-02: Education tab is visible when content exists but no education_progress row', async ({ page }) => {
        page._jsErrors = [];
        page.on('pageerror', err => page._jsErrors.push(err.message));
        // Phase is NOT education here -- the tab must still show because content exists.
        await bypassAuth(page, 'development');
        await gotoApp(page);

        await updateResults(page, {
            exists: true,
            progress: {},
            summary: {},
            content: { 'Emotional Awareness & Regulation': { what_this_means: 'Captured before any quiz.' } },
        }, 'development');

        const eduTab = page.locator('.results-tab', { hasText: 'Education' });
        await expect(eduTab).toBeVisible({ timeout: 5000 });
    });

    // ── Empty state ─────────────────────────────────────────────────────────

    test('journal-03: empty state renders when no content has been captured', async ({ page }) => {
        page._jsErrors = [];
        page.on('pageerror', err => page._jsErrors.push(err.message));
        await bypassAuth(page, 'education');
        await gotoApp(page);

        const empty = page.locator('.edu-journal__empty');
        await expect(empty).toBeVisible({ timeout: 5000 });
        await expect(empty).toContainText('Your learning notes will appear here as you go through each topic.');
        await expect(page.locator('.edu-journal__dim')).toHaveCount(0);
    });

    // ── Accordion rendering + ordering ──────────────────────────────────────

    test('journal-04: journal renders Dimension -> Category accordion in canonical order, only captured items shown', async ({ page }) => {
        page._jsErrors = [];
        page.on('pageerror', err => page._jsErrors.push(err.message));
        await bypassAuth(page, 'education');
        await gotoApp(page);
        await updateResults(page, MULTI_DIM_CONTENT, 'education');

        const dims = page.locator('.edu-journal__dim');
        await expect(dims).toHaveCount(2, { timeout: 5000 });
        // Canonical order: Meta-Cognitive Awareness before Mindful Presence,
        // even though the payload listed Mindful Presence first.
        await expect(dims.nth(0)).toContainText('Meta-Cognitive Awareness');
        await expect(dims.nth(1)).toContainText('Mindful Presence');

        // Only captured categories render within the first (expanded) dimension.
        const firstDimCats = dims.nth(0).locator('.edu-journal__cat');
        await expect(firstDimCats).toHaveCount(2);
        // Canonical category order: what_this_means before your_score.
        await expect(firstDimCats.nth(0)).toContainText('What This Means');
        await expect(firstDimCats.nth(1)).toContainText('Your Score');

        // Mindful Presence only captured one category.
        const secondDimCats = dims.nth(1).locator('.edu-journal__cat');
        await expect(secondDimCats).toHaveCount(1);
        await expect(secondDimCats.nth(0)).toContainText('What This Means');

        await page.screenshot({ path: `${SCREENSHOTS_DIR}/journal-04-all-dims-collapsed.png` });
    });

    test('journal-05: expanding a category reveals sanitized markdown content', async ({ page }) => {
        page._jsErrors = [];
        page.on('pageerror', err => page._jsErrors.push(err.message));
        await bypassAuth(page, 'education');
        await gotoApp(page);
        await updateResults(page, MULTI_DIM_CONTENT, 'education');

        // Expand the first dimension, then its first category.
        const firstDim = page.locator('.edu-journal__dim').nth(0);
        await firstDim.locator('.edu-journal__dim-header').click();
        const firstCat = firstDim.locator('.edu-journal__cat').nth(0);
        await firstCat.locator('.edu-journal__cat-header').click();

        const body = firstCat.locator('.edu-journal__body');
        await expect(body).toBeVisible({ timeout: 5000 });
        await expect(body.locator('strong')).toHaveText('Meta-Cognitive Awareness');
        await expect(body).toContainText('is thinking about your thinking.');

        await page.screenshot({ path: `${SCREENSHOTS_DIR}/journal-05-category-expanded.png` });
    });

    // ── Accessibility ───────────────────────────────────────────────────────

    test('journal-06: accordion headers are keyboard operable and aria-expanded flips', async ({ page }) => {
        page._jsErrors = [];
        page.on('pageerror', err => page._jsErrors.push(err.message));
        await bypassAuth(page, 'education');
        await gotoApp(page);
        await updateResults(page, MULTI_DIM_CONTENT, 'education');

        const dimHeader = page.locator('.edu-journal__dim-header').first();
        await expect(dimHeader).toHaveAttribute('aria-expanded', 'false');

        await dimHeader.focus();
        await page.keyboard.press('Enter');
        await expect(dimHeader).toHaveAttribute('aria-expanded', 'true');

        // Category header inside the now-expanded dimension.
        const catHeader = page.locator('.edu-journal__dim').first().locator('.edu-journal__cat-header').first();
        await expect(catHeader).toHaveAttribute('aria-expanded', 'false');
        await catHeader.focus();
        await page.keyboard.press('Space');
        await expect(catHeader).toHaveAttribute('aria-expanded', 'true');

        // Toggling back closed via click works too (Enter/Space + click parity).
        await dimHeader.click();
        await expect(dimHeader).toHaveAttribute('aria-expanded', 'false');
    });

    test('journal-07: chevrons are aria-hidden', async ({ page }) => {
        page._jsErrors = [];
        page.on('pageerror', err => page._jsErrors.push(err.message));
        await bypassAuth(page, 'education');
        await gotoApp(page);
        await updateResults(page, MULTI_DIM_CONTENT, 'education');

        const chevrons = page.locator('.edu-journal__chevron');
        const count = await chevrons.count();
        expect(count).toBeGreaterThan(0);
        for (let i = 0; i < count; i++) {
            await expect(chevrons.nth(i)).toHaveAttribute('aria-hidden', 'true');
        }
    });

    // ── XSS ──────────────────────────────────────────────────────────────────

    test('journal-08: captured content renders inert on XSS payload', async ({ page }) => {
        page._jsErrors = [];
        page.on('pageerror', err => page._jsErrors.push(err.message));
        await bypassAuth(page, 'education');
        await gotoApp(page);
        await updateResults(page, {
            exists: true,
            progress: {},
            summary: {},
            content: {
                'Emotional Awareness & Regulation': {
                    what_this_means: 'Some text <img src=x onerror="window._xssFired = true">',
                },
            },
        }, 'education');

        const dimHeader = page.locator('.edu-journal__dim-header').first();
        await dimHeader.click();
        const catHeader = page.locator('.edu-journal__cat-header').first();
        await catHeader.click();

        const body = page.locator('.edu-journal__body').first();
        await expect(body).toBeVisible({ timeout: 5000 });
        await expect(body.locator('img')).toHaveCount(0);
        const xssFired = await page.evaluate(() => window._xssFired);
        expect(xssFired).toBeUndefined();
    });
});
