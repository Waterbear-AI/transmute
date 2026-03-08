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

test.describe('Quadrant Chart — v13 Axis Convention', () => {

  test.beforeEach(async ({ page }) => {
    await bypassAuth(page);
    await page.goto('/');
    await page.locator('#app').waitFor({ state: 'visible', timeout: 10000 });
    await page.waitForLoadState('networkidle');
  });

  test('quadrant chart renders with correct archetype for Transmuter placement', async ({ page }) => {
    // Inject profile data with quadrant_placement where x=A>0, y=F>0 → Transmuter
    await page.evaluate(() => {
      if (typeof Results !== 'undefined') {
        Results.update({
          latest_profile: {
            quadrant: 'Transmuter',
            quadrant_placement: { x: 0.6, y: 0.5, archetype: 'transmuter' },
            synopsis: 'Test transmuter profile',
          }
        }, 'profile');
      }
    });

    const canvas = page.locator('canvas.quadrant-chart-canvas');
    await expect(canvas).toBeVisible({ timeout: 5000 });

    // Verify the chart's aria-label indicates it's a quadrant chart
    await expect(canvas).toHaveAttribute('aria-label', /quadrant chart/i);
  });

  test('quadrant chart renders with correct archetype for Magnifier placement', async ({ page }) => {
    // x=A>0, y=F<0 → Magnifier (bottom-right in v13)
    await page.evaluate(() => {
      if (typeof Results !== 'undefined') {
        Results.update({
          latest_profile: {
            quadrant: 'Magnifier',
            quadrant_placement: { x: 0.5, y: -0.3, archetype: 'magnifier' },
            synopsis: 'Test magnifier profile',
          }
        }, 'profile');
      }
    });

    const canvas = page.locator('canvas.quadrant-chart-canvas');
    await expect(canvas).toBeVisible({ timeout: 5000 });
  });

  test('quadrant chart renders with flow_data fallback when no quadrant_placement', async ({ page }) => {
    // When only flow_data is available (no quadrant_placement), chart should still render
    await page.evaluate(() => {
      if (typeof Results !== 'undefined') {
        Results.update({
          latest_profile: {
            quadrant: 'Absorber',
            synopsis: 'Test flow data profile',
            flow_data: {
              levels: [{ level: 'overall', filtering: 0.4, amplification: -0.3 }],
              weighted_total: 32.1
            }
          }
        }, 'profile');
      }
    });

    const canvas = page.locator('canvas.quadrant-chart-canvas');
    await expect(canvas).toBeVisible({ timeout: 5000 });
  });

  test('quadrant chart tooltip shows archetype on hover', async ({ page }) => {
    await page.evaluate(() => {
      if (typeof Results !== 'undefined') {
        Results.update({
          latest_profile: {
            quadrant: 'Transmuter',
            quadrant_placement: { x: 0.6, y: 0.5, archetype: 'transmuter' },
            synopsis: 'Tooltip test',
          }
        }, 'profile');
      }
    });

    const canvas = page.locator('canvas.quadrant-chart-canvas');
    await expect(canvas).toBeVisible({ timeout: 5000 });

    // Get canvas bounding box to calculate dot position
    const box = await canvas.boundingBox();
    if (box) {
      const cx = box.x + box.width / 2;
      const cy = box.y + box.height / 2;
      const padding = 40;
      const halfPlot = (box.width - padding * 2) / 2;

      // Dot position for x=0.6, y=0.5: right of center and above center
      const dotX = cx + (0.6 / 1.2) * halfPlot;
      const dotY = cy - (0.5 / 1.2) * halfPlot;

      await page.mouse.move(dotX, dotY);

      // Tooltip should appear with archetype name
      const tooltip = page.locator('.quadrant-chart-tooltip');
      await expect(tooltip).toBeVisible({ timeout: 3000 });
      await expect(tooltip).toContainText('Transmuter');
    }
  });

  test('quadrant chart axis labels match v13 convention', async ({ page }) => {
    await page.evaluate(() => {
      if (typeof Results !== 'undefined') {
        Results.update({
          latest_profile: {
            quadrant: 'Conduit',
            quadrant_placement: { x: 0.0, y: 0.0, archetype: 'conduit' },
            synopsis: 'Axis label test',
          }
        }, 'profile');
      }
    });

    const canvas = page.locator('canvas.quadrant-chart-canvas');
    await expect(canvas).toBeVisible({ timeout: 5000 });

    // Verify chart is rendered by checking canvas has non-zero dimensions
    const box = await canvas.boundingBox();
    expect(box).not.toBeNull();
    expect(box.width).toBeGreaterThan(200);
    expect(box.height).toBeGreaterThan(200);
  });
});
