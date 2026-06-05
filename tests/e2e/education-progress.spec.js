// @ts-check
/**
 * E2E spec for the Education Progress tab.
 *
 * Verifies the fix where the tab only rendered categories already present in
 * the saved progress JSON — so untouched categories (e.g. "Category 5:
 * External Interaction") were missing and the completed-count denominator was
 * wrong (3/4 instead of 3/5). The tab must now always show all 5 canonical
 * categories per dimension, with untouched ones at 0%.
 */
const { test, expect } = require('@playwright/test');
const path = require('path');

const SCREENSHOTS_DIR = path.join(__dirname, 'screenshots');

async function bypassAuth(page) {
    await page.route('**/auth/me', route => route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ user_id: 'test-user', name: 'Test User', email: 'test@example.com', current_phase: 'education' }),
    }));
    await page.route('**/api/sessions', route => route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ sessions: [], count: 0, user_total_cost_usd: 0 }),
    }));
    await page.route('**/api/results/**', route => route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({}),
    }));
}

// Only 4 categories touched (external_interaction absent), 3 complete.
const EDUCATION_DATA = {
    exists: true,
    progress: {
        'Emotional Awareness': {
            what_this_means: { understanding_score: 100 },
            your_score: { understanding_score: 100 },
            daily_effects: { understanding_score: 100 },
            strengths_gaps: { understanding_score: 0 },
            // external_interaction intentionally absent
        },
    },
    summary: { total_categories: 5, completed_categories: 3, completion_pct: 60.0 },
};

async function showEducationTab(page, data) {
    await page.evaluate((d) => {
        if (typeof Results !== 'undefined') {
            Results.update({ education: d }, 'education');
        }
    }, data);
    await page.waitForTimeout(300);
}

test.describe('Education Progress tab', () => {

    test.beforeEach(async ({ page }) => {
        page._jsErrors = [];
        page.on('pageerror', err => page._jsErrors.push(err.message));

        await bypassAuth(page);
        await page.goto('/');
        await page.locator('#app').waitFor({ state: 'visible', timeout: 10000 });
        await page.waitForLoadState('networkidle');
    });

    test.afterEach(async ({ page }) => {
        const errs = page._jsErrors || [];
        if (errs.length > 0) throw new Error('Uncaught JS errors: ' + errs.join('; '));
    });

    test('edu-01: all 5 canonical categories render, including untouched External Interaction', async ({ page }) => {
        await showEducationTab(page, EDUCATION_DATA);

        const panel = page.locator('.results-dimension').filter({ hasText: 'Emotional Awareness' });
        await expect(panel).toBeVisible({ timeout: 5000 });

        // All 5 canonical category labels appear, in teaching order
        await expect(panel).toContainText('1. What This Means: 100%');
        await expect(panel).toContainText('2. Your Score: 100%');
        await expect(panel).toContainText('3. Daily Effects: 100%');
        await expect(panel).toContainText('4. Strengths & Gaps: 0%');
        // The previously-missing one — present even though it's untouched
        await expect(panel).toContainText('5. External Interaction: 0%');

        await page.screenshot({ path: `${SCREENSHOTS_DIR}/edu-01-all-categories.png` });
    });

    test('edu-02: summary shows the canonical 3 / 5 denominator', async ({ page }) => {
        await showEducationTab(page, EDUCATION_DATA);
        const summary = page.locator('.results-summary');
        await expect(summary).toBeVisible({ timeout: 5000 });
        await expect(summary).toContainText('3 / 5 categories completed (60%)');
    });
});
