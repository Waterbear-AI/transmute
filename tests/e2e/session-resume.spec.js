// @ts-check
const { test, expect } = require('@playwright/test');

/**
 * Helper: bypass auth overlay and show the main app with an active session.
 */
async function bypassAuthWithSession(page, sessionId = 'test-session-id') {
  await page.route('**/auth/me', route => {
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ user_id: 'test-user', name: 'Test User', email: 'test@example.com' })
    });
  });

  // Mock session list with one session
  await page.route('**/api/sessions', route => {
    if (route.request().method() === 'GET') {
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          sessions: [{
            session_id: sessionId,
            user_id: 'test-user',
            app_name: 'transmutation',
            archived: false,
            created_at: new Date(Date.now() - 3600000).toISOString(),
            message_count: 3
          }],
          count: 1
        })
      });
    } else {
      // POST /api/sessions — create new
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          session_id: 'new-session-id',
          user_id: 'test-user',
          app_name: 'transmutation',
          archived: false,
          created_at: new Date().toISOString(),
          message_count: 0
        })
      });
    }
  });

  await page.route('**/api/results/**', route => {
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({})
    });
  });
}

test.describe('Session Resume (FE-001)', () => {

  test.beforeEach(async ({ page }) => {
    await bypassAuthWithSession(page);
  });

  test('session bar shows message count badge', async ({ page }) => {
    await page.goto('/');
    await page.locator('#app').waitFor({ state: 'visible', timeout: 10000 });
    await page.waitForLoadState('networkidle');

    // Session entry should exist in the session bar
    const sessionBar = page.locator('#session-list');
    await expect(sessionBar).toBeVisible();

    // A session button should display the message count
    const sessionButtons = sessionBar.locator('.session-btn:not(.session-btn--new):not(.session-btn--reset)');
    const count = await sessionButtons.count();
    expect(count).toBeGreaterThan(0);

    // The session label should include message count reference (msgs)
    const firstBtn = sessionButtons.first();
    await expect(firstBtn).toBeVisible();
    const text = await firstBtn.textContent();
    expect(text).toContain('msgs');
  });

  test('session bar includes semantic New and Start Over buttons', async ({ page }) => {
    await page.goto('/');
    await page.locator('#app').waitFor({ state: 'visible', timeout: 10000 });
    await page.waitForLoadState('networkidle');

    const newBtn = page.locator('.session-btn--new');
    const resetBtn = page.locator('.session-btn--reset');

    await expect(newBtn).toBeVisible();
    await expect(resetBtn).toBeVisible();

    // Both must be semantic button elements
    await expect(newBtn).toHaveJSProperty('tagName', 'BUTTON');
    await expect(resetBtn).toHaveJSProperty('tagName', 'BUTTON');

    await expect(newBtn).toHaveText('New');
    await expect(resetBtn).toHaveText('Start Over');
  });

  test('session buttons are keyboard-accessible', async ({ page }) => {
    await page.goto('/');
    await page.locator('#app').waitFor({ state: 'visible', timeout: 10000 });
    await page.waitForLoadState('networkidle');

    const resetBtn = page.locator('.session-btn--reset');
    await expect(resetBtn).toBeVisible();

    // Ensure the reset button is focusable via keyboard
    await resetBtn.focus();
    const focused = page.locator(':focus');
    await expect(focused).toHaveClass(/session-btn--reset/);
  });

  test('clicking session loads history via API', async ({ page }) => {
    const sessionId = 'test-session-id';

    // Mock the history endpoint
    let historyFetched = false;
    await page.route(`**/api/sessions/${sessionId}/history`, route => {
      historyFetched = true;
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          session_id: sessionId,
          messages: [
            { role: 'user', text: 'Hello from history' },
            { role: 'agent', text: 'Welcome back!' }
          ],
          answered_responses: {}
        })
      });
    });

    await page.goto('/');
    await page.locator('#app').waitFor({ state: 'visible', timeout: 10000 });
    await page.waitForLoadState('networkidle');

    // Click the session entry
    const sessionBtns = page.locator('.session-btn:not(.session-btn--new):not(.session-btn--reset)');
    const count = await sessionBtns.count();
    if (count > 0) {
      await sessionBtns.first().click();
      await page.waitForTimeout(500);
      expect(historyFetched).toBe(true);
    }
  });

  test('history messages render in chat area', async ({ page }) => {
    const sessionId = 'test-session-id';

    await page.route(`**/api/sessions/${sessionId}/history`, route => {
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          session_id: sessionId,
          messages: [
            { role: 'user', text: 'My question from history' },
            { role: 'agent', text: 'Agent response from history' }
          ],
          answered_responses: {}
        })
      });
    });

    await page.goto('/');
    await page.locator('#app').waitFor({ state: 'visible', timeout: 10000 });
    await page.waitForLoadState('networkidle');

    const sessionBtns = page.locator('.session-btn:not(.session-btn--new):not(.session-btn--reset)');
    const count = await sessionBtns.count();
    if (count > 0) {
      await sessionBtns.first().click();
      await page.waitForTimeout(700);

      // User message should appear
      const chatMessages = page.locator('#chat-messages');
      await expect(chatMessages).toContainText('My question from history');
    }
  });

  test('answered Likert widget renders as read-only in history', async ({ page }) => {
    const sessionId = 'test-session-id';
    const qid = 'q-test-001';

    await page.route(`**/api/sessions/${sessionId}/history`, route => {
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          session_id: sessionId,
          messages: [
            {
              role: 'widget',
              event_type: 'assessment.question_batch',
              data: {
                event_type: 'assessment.question_batch',
                batch_id: 'batch1',
                dimension: 'Moral Awareness',
                sub_dimension: 'Sensitivity',
                count: 1,
                questions: [{
                  id: qid,
                  text: 'I notice ethical dilemmas in everyday situations.',
                  scale_type: 'agreement',
                  scale_labels: ['Strongly Disagree', 'Disagree', 'Neutral', 'Agree', 'Strongly Agree']
                }]
              }
            }
          ],
          answered_responses: {
            [qid]: { score: 4, type: 'likert' }
          }
        })
      });
    });

    await page.goto('/');
    await page.locator('#app').waitFor({ state: 'visible', timeout: 10000 });
    await page.waitForLoadState('networkidle');

    const sessionBtns = page.locator('.session-btn:not(.session-btn--new):not(.session-btn--reset)');
    const count = await sessionBtns.count();
    if (count > 0) {
      await sessionBtns.first().click();
      await page.waitForTimeout(700);

      // Likert card should render
      const likertCard = page.locator('.widget-card').first();
      await expect(likertCard).toBeVisible({ timeout: 3000 });

      // The 4th option (Agree, index 3) should be selected
      const options = likertCard.locator('.likert-option');
      const fourthOption = options.nth(3);
      await expect(fourthOption).toHaveClass(/likert-option--selected/);

      // All options should be disabled in history mode
      const allOptions = await options.all();
      for (const opt of allOptions) {
        await expect(opt).toBeDisabled();
      }
    }
  });

});

test.describe('Session Reset (FE-001)', () => {

  test.beforeEach(async ({ page }) => {
    await bypassAuthWithSession(page);
  });

  test('Start Over button is visible and accessible', async ({ page }) => {
    await page.goto('/');
    await page.locator('#app').waitFor({ state: 'visible', timeout: 10000 });
    await page.waitForLoadState('networkidle');

    const resetBtn = page.locator('.session-btn--reset');
    await expect(resetBtn).toBeVisible();
    await expect(resetBtn).toBeEnabled();
    // Must be a button for keyboard accessibility
    await expect(resetBtn).toHaveJSProperty('tagName', 'BUTTON');
  });

  test('Start Over shows confirmation dialog before resetting', async ({ page }) => {
    await page.goto('/');
    await page.locator('#app').waitFor({ state: 'visible', timeout: 10000 });
    await page.waitForLoadState('networkidle');

    let dialogShown = false;
    page.on('dialog', async dialog => {
      dialogShown = true;
      // Dismiss without confirming to avoid page reload in test
      await dialog.dismiss();
    });

    await page.locator('.session-btn--reset').click();
    await page.waitForTimeout(300);

    expect(dialogShown).toBe(true);
  });

  test('Start Over calls POST /api/sessions/reset on confirmation', async ({ page }) => {
    let resetCalled = false;

    await page.route('**/api/sessions/reset', route => {
      resetCalled = true;
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          session_id: 'fresh-session-id',
          user_id: 'test-user',
          app_name: 'transmutation',
          archived: false,
          created_at: new Date().toISOString(),
          message_count: 0
        })
      });
    });

    await page.goto('/');
    await page.locator('#app').waitFor({ state: 'visible', timeout: 10000 });
    await page.waitForLoadState('networkidle');

    // Accept the confirmation dialog
    page.on('dialog', dialog => dialog.accept());

    await page.locator('.session-btn--reset').click();
    await page.waitForTimeout(500);

    expect(resetCalled).toBe(true);
  });

});
