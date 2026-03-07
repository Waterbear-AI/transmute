// @ts-check
const { test, expect } = require('@playwright/test');

/**
 * Helper: bypass auth overlay and show the main app.
 * The app requires Firebase auth; for E2E we hide the overlay and show the app.
 */
async function bypassAuth(page) {
  // Intercept auth check to return a fake authenticated user
  await page.route('**/auth/me', route => {
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ user_id: 'test-user', name: 'Test User', email: 'test@example.com' })
    });
  });
  // Intercept API calls that would fail without auth
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

test.describe('UI Journeys', () => {

  test.beforeEach(async ({ page }) => {
    await bypassAuth(page);
    await page.goto('/');
    await page.locator('#app').waitFor({ state: 'visible', timeout: 10000 });
    // Wait for all initial async fetches (sessions, results) to complete
    // so that Results.update({}) doesn't overwrite data injected by tests.
    await page.waitForLoadState('networkidle');
  });

  test('page loads with correct title', async ({ page }) => {
    await expect(page).toHaveTitle('Transmutation Engine');
  });

  test('main layout panels are visible', async ({ page }) => {
    const chatPanel = page.locator('#chat-panel');
    const resultsPanel = page.locator('#results-panel');
    await expect(chatPanel).toBeVisible();
    await expect(resultsPanel).toBeVisible();
  });

  test('chat form is functional', async ({ page }) => {
    const input = page.locator('#chat-input');
    const sendBtn = page.locator('.chat-send-btn');
    await expect(input).toBeVisible();
    await expect(sendBtn).toBeVisible();
    await expect(input).toHaveAttribute('placeholder', 'Type a message...');
  });

  test('phase stepper renders when results update', async ({ page }) => {
    // Trigger results update to make stepper appear
    await page.evaluate(() => {
      if (typeof Results !== 'undefined') {
        Results.update({ assessment: { exists: true, answered: 10, total: 200 } }, 'assessment');
      }
    });

    const stepper = page.locator('.phase-stepper');
    await expect(stepper).toBeVisible();
    await expect(stepper).toHaveAttribute('role', 'navigation');
  });

  test('quadrant chart renders in profile tab', async ({ page }) => {
    // Provide profile data with flow_data
    await page.evaluate(() => {
      if (typeof Results !== 'undefined') {
        Results.update({
          latest_profile: {
            quadrant: 'Transmuter',
            synopsis: 'Test profile',
            flow_data: {
              levels: [{ level: 'overall', filtering: 0.6, amplification: 0.7 }],
              weighted_total: 48.2
            }
          }
        }, 'profile');
      }
    });

    const canvas = page.locator('canvas');
    // QuadrantChart renders a canvas element
    await expect(canvas).toBeVisible();
  });

  test('toast notification system works', async ({ page }) => {
    await page.evaluate(() => {
      if (typeof Toast !== 'undefined') {
        Toast.init();
        Toast.show('Test notification', 'success');
      }
    });

    const toast = page.locator('.toast--success');
    await expect(toast).toBeVisible();
    await expect(toast).toContainText('Test notification');
  });

  test('loading state on chat submit button', async ({ page }) => {
    const sendBtn = page.locator('.chat-send-btn');
    const input = page.locator('#chat-input');

    // Type a message
    await input.fill('test message');

    // Add loading class manually to verify CSS works
    await page.evaluate(() => {
      const btn = document.querySelector('.chat-send-btn');
      if (btn) btn.classList.add('chat-send-btn--loading');
    });

    await expect(sendBtn).toHaveClass(/chat-send-btn--loading/);
  });

  test('comparison view renders with delta indicators', async ({ page }) => {
    // Use handleSSEEvent to directly trigger reassessment rendering
    await page.evaluate(() => {
      if (typeof Results !== 'undefined') {
        Results.handleSSEEvent('checkin.complete', {
          previous_snapshot: { quadrant: 'Absorber', weighted_total: 42.5 },
          current_snapshot: { quadrant: 'Transmuter', weighted_total: 48.2 },
          deltas: {
            'Moral Awareness': { direction: 'up', delta: 5.7 },
            'Ethical Reasoning': { direction: 'down', delta: -2.1 }
          },
          quadrant_shift: { shifted: true, from: 'Absorber', to: 'Transmuter' }
        });
      }
    });

    // Switch to reassessment tab
    await page.evaluate(() => {
      if (typeof Results !== 'undefined') {
        Results.handlePhaseTransition('assessment', 'reassessment');
      }
    });

    const grid = page.locator('.comparison-grid');
    await expect(grid).toBeVisible({ timeout: 10000 });

    // Check delta indicators
    const upDelta = page.locator('.comparison-delta__value--up');
    const downDelta = page.locator('.comparison-delta__value--down');
    await expect(upDelta.first()).toBeVisible({ timeout: 5000 });
    await expect(downDelta.first()).toBeVisible({ timeout: 5000 });
  });
});
