// @ts-check
const { test, expect } = require('@playwright/test');

/**
 * Helper: bypass auth overlay and show the main app.
 */
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

test.describe('Accessibility', () => {

  test.beforeEach(async ({ page }) => {
    await bypassAuth(page);
    await page.goto('/');
    await page.locator('#app').waitFor({ state: 'visible', timeout: 10000 });
  });

  test('skip link is present and focusable', async ({ page }) => {
    const skipLink = page.locator('.skip-link');
    await expect(skipLink).toHaveAttribute('href', '#chat-panel');

    // Tab to the skip link
    await page.keyboard.press('Tab');
    const focused = page.locator(':focus');
    await expect(focused).toHaveClass(/skip-link/);
  });

  test('chat input has accessible label', async ({ page }) => {
    const label = page.locator('label[for="chat-input"]');
    await expect(label).toBeAttached();
    await expect(label).toHaveText('Chat message');
  });

  test('chat messages container has aria-live', async ({ page }) => {
    const messagesEl = page.locator('#chat-messages');
    await expect(messagesEl).toHaveAttribute('aria-live', 'polite');
  });

  test('focus-visible outlines are styled', async ({ page }) => {
    // Verify that :focus-visible CSS is defined
    const hasOutline = await page.evaluate(() => {
      const style = document.createElement('style');
      document.head.appendChild(style);
      const rules = Array.from(document.styleSheets)
        .flatMap(s => { try { return Array.from(s.cssRules); } catch { return []; } })
        .filter(r => r.cssText && r.cssText.includes('focus-visible'));
      style.remove();
      return rules.length > 0;
    });
    expect(hasOutline).toBe(true);
  });

  test('results tabs have correct ARIA roles', async ({ page }) => {
    // Trigger results to show tabs
    await page.evaluate(() => {
      if (typeof Results !== 'undefined') {
        Results.update({ assessment: { exists: true, answered: 5, total: 200 } }, 'assessment');
      }
    });

    const tablist = page.locator('[role="tablist"]');
    await expect(tablist).toBeVisible();

    const tabs = page.locator('[role="tab"]');
    const count = await tabs.count();
    expect(count).toBeGreaterThan(0);

    // Active tab should have aria-selected="true"
    const activeTab = page.locator('[role="tab"][aria-selected="true"]');
    await expect(activeTab).toBeVisible();
  });
});

test.describe('Responsive Layout', () => {

  test('480px viewport: tabs are scrollable and session bar wraps', async ({ page }) => {
    await page.setViewportSize({ width: 480, height: 800 });
    await bypassAuth(page);
    await page.goto('/');
    await page.locator('#app').waitFor({ state: 'visible', timeout: 10000 });

    // Trigger enough tabs to test scrolling
    await page.evaluate(() => {
      if (typeof Results !== 'undefined') {
        Results.update({
          assessment: { exists: true, answered: 10, total: 200 },
          latest_profile: { quadrant: 'Transmuter', synopsis: 'Test' },
          education: { exists: true, progress: {} }
        }, 'education');
      }
    });

    // Verify tabs container has overflow-x
    const tabsOverflow = await page.evaluate(() => {
      const tabs = document.querySelector('.results-tabs');
      return tabs ? getComputedStyle(tabs).overflowX : null;
    });
    expect(tabsOverflow).toBe('auto');

    // Verify session list can wrap
    const sessionWrap = await page.evaluate(() => {
      const list = document.querySelector('.session-list');
      return list ? getComputedStyle(list).flexWrap : null;
    });
    expect(sessionWrap).toBe('wrap');
  });

  test('768px viewport: layout stacks vertically', async ({ page }) => {
    await page.setViewportSize({ width: 768, height: 1024 });
    await bypassAuth(page);
    await page.goto('/');
    await page.locator('#app').waitFor({ state: 'visible', timeout: 10000 });

    const direction = await page.evaluate(() => {
      const panels = document.querySelector('.main-panels');
      return panels ? getComputedStyle(panels).flexDirection : null;
    });
    expect(direction).toBe('column');
  });
});
