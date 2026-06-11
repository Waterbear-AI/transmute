// @ts-check
/**
 * E2E tests for the Development tab UI (FE-001).
 *
 * Tests run against a mocked /api/results payload so they work without a
 * live seeded DB.  A page.on('pageerror') listener is registered in every
 * test — zero JS errors are asserted after each interaction.
 *
 * Scenarios covered (spec B13.2 [UI] criteria):
 *  - Practice cards render with title / dimension·operation / entry stats
 *  - Gate block shows both progress bars with ARIA attributes (FR-6, FR-8)
 *  - Ready-for-reassessment banner visible when gate.passed + phase=development (FR-7)
 *  - Ready banner absent when phase != development (phase-gated suppression, FR-7)
 *  - Empty state when roadmap exists but zero entries (FR-10)
 *  - No-roadmap empty state (FR-10)
 *  - Journal section lists entries newest-first with date, rating, reflection (FR-5)
 */

const { test, expect } = require('@playwright/test');

// ── Shared mock payloads ──────────────────────────────────────────────────────

const SEEDED_DEVELOPMENT_RESULTS = {
  user_id: 'dev-test-user',
  current_phase: 'development',
  assessment: { exists: false },
  profiles: [],
  latest_profile: null,
  education: null,
  development: {
    has_roadmap: true,
    roadmap: { steps: [] },
    practice_count: 6,
    roadmap_created_at: new Date(Date.now() - 12 * 86400000).toISOString(),
    total_entries: 6,
    practices: [
      {
        practice_id: 'p1',
        title: 'Noticing scarcity narratives',
        dimension: 'Emotional Awareness',
        sub_dimension: 'Emotion Recognition',
        transmutation_operation: 'filtering',
        entry_count: 3,
        last_self_rating: 7,
        last_entry_at: new Date(Date.now() - 2 * 86400000).toISOString(),
      },
      {
        practice_id: 'p2',
        title: 'Body scan',
        dimension: 'Physical Awareness',
        sub_dimension: null,
        transmutation_operation: null,
        entry_count: 2,
        last_self_rating: 6,
        last_entry_at: new Date(Date.now() - 1 * 86400000).toISOString(),
      },
      {
        practice_id: 'p3',
        title: 'Values reflection',
        dimension: 'Mindfulness',
        sub_dimension: null,
        transmutation_operation: 'amplification',
        entry_count: 1,
        last_self_rating: 8,
        last_entry_at: new Date().toISOString(),
      },
    ],
    recent_entries: [
      {
        practice_id: 'p3',
        reflection: 'I caught myself spiraling about money and paused to name it.',
        self_rating: 7,
        dimension: 'Emotional Awareness',
        created_at: new Date(Date.now() - 1 * 3600000).toISOString(),
      },
      {
        practice_id: 'p2',
        reflection: 'Body scan felt grounding today.',
        self_rating: 6,
        dimension: 'Physical Awareness',
        created_at: new Date(Date.now() - 25 * 3600000).toISOString(),
      },
    ],
    gate: {
      entries_logged: 6,
      entries_required: 10,
      days_elapsed: 12,
      days_required: 30,
      passed: false,
      via: null,
    },
  },
  graduation: null,
  check_ins: null,
};

const GATE_PASSED_RESULTS = JSON.parse(JSON.stringify(SEEDED_DEVELOPMENT_RESULTS));
GATE_PASSED_RESULTS.development.gate.passed = true;
GATE_PASSED_RESULTS.development.gate.via = 'entries';
GATE_PASSED_RESULTS.development.gate.entries_logged = 10;
GATE_PASSED_RESULTS.development.practice_count = 10;
GATE_PASSED_RESULTS.development.total_entries = 10;

const REASSESSMENT_GATE_PASSED_RESULTS = JSON.parse(JSON.stringify(GATE_PASSED_RESULTS));
REASSESSMENT_GATE_PASSED_RESULTS.current_phase = 'reassessment';

const NO_ENTRIES_RESULTS = JSON.parse(JSON.stringify(SEEDED_DEVELOPMENT_RESULTS));
NO_ENTRIES_RESULTS.development.practice_count = 0;
NO_ENTRIES_RESULTS.development.total_entries = 0;
NO_ENTRIES_RESULTS.development.recent_entries = [];
NO_ENTRIES_RESULTS.development.gate.entries_logged = 0;
NO_ENTRIES_RESULTS.development.gate.days_elapsed = 0;
for (let p of NO_ENTRIES_RESULTS.development.practices) {
  p.entry_count = 0;
  p.last_self_rating = null;
  p.last_entry_at = null;
}

const NO_ROADMAP_RESULTS = {
  user_id: 'dev-noroadmap-user',
  current_phase: 'development',
  assessment: { exists: false },
  profiles: [],
  latest_profile: null,
  education: null,
  development: {
    has_roadmap: false,
    roadmap: null,
    practice_count: 0,
    roadmap_created_at: null,
    total_entries: 0,
    practices: [],
    recent_entries: [],
    gate: {
      entries_logged: 0,
      entries_required: 10,
      days_elapsed: null,
      days_required: 30,
      passed: false,
      via: null,
    },
  },
  graduation: null,
  check_ins: null,
};

// ── Helpers ───────────────────────────────────────────────────────────────────

/**
 * Mock auth + results endpoints, navigate to the app, and activate the
 * Development tab by faking a development-phase user.
 */
async function setupDevTab(page, resultsPayload) {
  // Collect JS errors
  const jsErrors = [];
  page.on('pageerror', err => jsErrors.push(err.message));

  // Mock auth — include current_phase so Results.update() sets _currentPhase correctly.
  // Without it, app.js passes undefined and the phase-gated ready-banner and
  // no-roadmap empty state (which require _currentPhase === 'development') never fire.
  await page.route('**/auth/me', route => {
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        user_id: resultsPayload.user_id,
        name: 'Dev User',
        email: 'dev@test.example.com',
        current_phase: resultsPayload.current_phase,
      }),
    });
  });
  await page.route('**/api/sessions', route => {
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ sessions: [] }),
    });
  });
  await page.route('**/api/results/**', route => {
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(resultsPayload),
    });
  });

  await page.goto('/');
  await page.locator('#app').waitFor({ state: 'visible', timeout: 10000 });

  // Click the Development tab if visible
  const devTab = page.locator('.results-tab', { hasText: 'Development' });
  await devTab.waitFor({ state: 'visible', timeout: 5000 });
  await devTab.click();

  return jsErrors;
}

// ── Tests ─────────────────────────────────────────────────────────────────────

test.describe('Development tab', () => {

  test('renders 3 practice cards with title, dimension, and entry stats', async ({ page }) => {
    const jsErrors = await setupDevTab(page, SEEDED_DEVELOPMENT_RESULTS);

    // Wait for content to render
    await page.locator('.practice-card').first().waitFor({ state: 'visible', timeout: 5000 });

    const cards = page.locator('.practice-card');
    await expect(cards).toHaveCount(3);

    // First card assertions
    const firstCard = cards.first();
    await expect(firstCard.locator('h5')).toContainText('Noticing scarcity narratives');
    await expect(firstCard.locator('.practice-card__meta')).toContainText('Emotional Awareness');
    await expect(firstCard.locator('.practice-card__meta')).toContainText('filtering');
    await expect(firstCard.locator('.practice-card__stats')).toContainText('3 entries');
    await expect(firstCard.locator('.practice-card__stats')).toContainText('7/10');

    expect(jsErrors, 'JS errors: ' + jsErrors.join('; ')).toHaveLength(0);
  });

  test('shows both gate progress bars with ARIA attributes (FR-6, FR-8)', async ({ page }) => {
    const jsErrors = await setupDevTab(page, SEEDED_DEVELOPMENT_RESULTS);

    await page.locator('.dev-gate-block').waitFor({ state: 'visible', timeout: 5000 });

    // Both progress bars inside the gate block
    const gateBlock = page.locator('.dev-gate-block');
    const bars = gateBlock.locator('[role="progressbar"]');
    await expect(bars).toHaveCount(2);

    // Verify ARIA attributes on both bars
    const allBars = await bars.all();
    for (const bar of allBars) {
      await expect(bar).toHaveAttribute('aria-valuemin', '0');
      await expect(bar).toHaveAttribute('aria-valuemax', '100');
      // aria-valuenow should be a number string
      const valuenow = await bar.getAttribute('aria-valuenow');
      expect(Number(valuenow)).toBeGreaterThanOrEqual(0);
    }

    // Entries label text
    await expect(page.locator('.dev-gate-block')).toContainText('6 / 10');
    // Days label text
    await expect(page.locator('.dev-gate-block')).toContainText('12 / 30 days');

    expect(jsErrors, 'JS errors: ' + jsErrors.join('; ')).toHaveLength(0);
  });

  test('shows ready-for-reassessment banner when gate passed and phase=development (FR-7)', async ({ page }) => {
    const jsErrors = await setupDevTab(page, GATE_PASSED_RESULTS);

    const banner = page.locator('.ready-banner');
    await banner.waitFor({ state: 'visible', timeout: 5000 });
    await expect(banner).toHaveAttribute('role', 'status');
    await expect(banner).toContainText('Ready for reassessment');
    await expect(banner).toContainText('chat');

    expect(jsErrors, 'JS errors: ' + jsErrors.join('; ')).toHaveLength(0);
  });

  test('does NOT show ready banner when phase is reassessment (phase-gated suppression, FR-7)', async ({ page }) => {
    const jsErrors = await setupDevTab(page, REASSESSMENT_GATE_PASSED_RESULTS);

    // Wait for dev tab content
    await page.locator('.dev-gate-block').waitFor({ state: 'visible', timeout: 5000 });
    await expect(page.locator('.ready-banner')).toHaveCount(0);

    expect(jsErrors, 'JS errors: ' + jsErrors.join('; ')).toHaveLength(0);
  });

  test('shows journal entries newest-first with date, rating, reflection (FR-5)', async ({ page }) => {
    const jsErrors = await setupDevTab(page, SEEDED_DEVELOPMENT_RESULTS);

    await page.locator('.journal-entry').first().waitFor({ state: 'visible', timeout: 5000 });

    const entries = page.locator('.journal-entry');
    await expect(entries).toHaveCount(2);

    // First entry (newest) should contain the reflection text
    const firstEntry = entries.first();
    await expect(firstEntry.locator('.journal-entry__reflection')).toContainText('spiraling about money');
    await expect(firstEntry.locator('.journal-entry__meta')).toContainText('7/10');

    expect(jsErrors, 'JS errors: ' + jsErrors.join('; ')).toHaveLength(0);
  });

  test('shows empty-state when roadmap exists but zero entries (FR-10)', async ({ page }) => {
    const jsErrors = await setupDevTab(page, NO_ENTRIES_RESULTS);

    await page.locator('.dev-journal-section').waitFor({ state: 'visible', timeout: 5000 });
    await expect(page.locator('.dev-journal-section')).toContainText('No journal entries yet');

    expect(jsErrors, 'JS errors: ' + jsErrors.join('; ')).toHaveLength(0);
  });

  test('shows no-roadmap empty state when has_roadmap is false (FR-10)', async ({ page }) => {
    const jsErrors = await setupDevTab(page, NO_ROADMAP_RESULTS);

    // Wait for dev tab content
    const content = page.locator('#results-content');
    await content.waitFor({ state: 'visible', timeout: 5000 });
    await expect(content).toContainText('No roadmap yet');
    await expect(content).toContainText('chat');

    // No practice cards or journal section when there's no roadmap
    await expect(page.locator('.practice-card')).toHaveCount(0);

    expect(jsErrors, 'JS errors: ' + jsErrors.join('; ')).toHaveLength(0);
  });

  test('all progress bars on the page have role=progressbar and aria-valuenow (FR-8)', async ({ page }) => {
    const jsErrors = await setupDevTab(page, SEEDED_DEVELOPMENT_RESULTS);

    // Wait for content to settle
    await page.locator('.progress-bar').first().waitFor({ state: 'visible', timeout: 5000 });

    const allBars = page.locator('[role="progressbar"]');
    const count = await allBars.count();
    expect(count).toBeGreaterThan(0);

    for (let i = 0; i < count; i++) {
      const bar = allBars.nth(i);
      await expect(bar).toHaveAttribute('aria-valuemin', '0');
      await expect(bar).toHaveAttribute('aria-valuemax', '100');
      const now = await bar.getAttribute('aria-valuenow');
      expect(now).not.toBeNull();
    }

    expect(jsErrors, 'JS errors: ' + jsErrors.join('; ')).toHaveLength(0);
  });

});
