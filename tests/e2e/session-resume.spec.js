// @ts-check
const { test, expect } = require('@playwright/test');
const path = require('path');
const fs = require('fs');

// Serve the updated sessions.js so tests exercise the new multi-session module
// regardless of which version is deployed in the running container.
const SESSIONS_JS_PATH = path.join(__dirname, '../../frontend/js/sessions.js');

/**
 * Helper: bypass auth overlay, inject updated sessions.js, and show the main
 * app with a mock active session.
 *
 * Updated for multi-session behaviour: sessions are rendered in #session-tabs
 * (tab strip inside the chat panel) rather than as .session-btn in #session-list.
 * The bottom bar (#session-list) now only contains the "Start Over" button.
 */
async function bypassAuthWithSession(page, sessionId = 'test-session-id') {
  // Serve updated sessions.js from source so selectors stay in sync
  const sessionsJsContent = fs.readFileSync(SESSIONS_JS_PATH, 'utf-8');
  await page.route('**/js/sessions.js', route => route.fulfill({
    status: 200,
    contentType: 'application/javascript',
    body: sessionsJsContent,
  }));

  await page.route('**/auth/me', route => {
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ user_id: 'test-user', name: 'Test User', email: 'test@example.com' })
    });
  });

  // Mock session list with one session (includes title field from migration 008)
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
            message_count: 3,
            title: null,
          }],
          count: 1,
          user_total_cost_usd: 0,
        })
      });
    } else {
      // POST /api/sessions — create new (multi-session: archive_prior=false)
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          session_id: 'new-session-id',
          user_id: 'test-user',
          app_name: 'transmutation',
          archived: false,
          created_at: new Date().toISOString(),
          message_count: 0,
          title: null,
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
    // Track JavaScript errors — uncaught exceptions must be 0
    const jsErrors = [];
    page.on('pageerror', err => jsErrors.push(err.message));
    page._jsErrors = jsErrors;

    await bypassAuthWithSession(page);
  });

  test('session tab strip shows session with message count label', async ({ page }) => {
    await page.goto('/');
    await page.locator('#app').waitFor({ state: 'visible', timeout: 10000 });
    await page.waitForLoadState('networkidle');
    await page.waitForTimeout(200);

    // New sessions.js renders tabs into #session-tabs (inside chat panel)
    const tabstrip = page.locator('#session-tabs');
    // tabstrip may be dynamically created; check via JS
    const tabstripExists = await page.evaluate(() => !!document.getElementById('session-tabs'));
    expect(tabstripExists).toBe(true);

    // Session tab (no title → uses _formatSessionLabel → shows "N msgs")
    const sessionTabs = await page.evaluate(() =>
      Array.from(
        document.querySelectorAll('#session-tabs .session-tab:not(.session-tab--new)')
      ).map(t => t.textContent.trim())
    );
    expect(sessionTabs.length).toBeGreaterThan(0);
    // message_count=3 → label contains "3 msgs"
    expect(sessionTabs[0]).toContain('msgs');

    // Verify 0 uncaught JavaScript errors
    expect(page._jsErrors, `JS errors: ${page._jsErrors.join(', ')}`).toHaveLength(0);
  });

  test('tab strip includes "+" New button and Start Over in bottom bar', async ({ page }) => {
    await page.goto('/');
    await page.locator('#app').waitFor({ state: 'visible', timeout: 10000 });
    await page.waitForLoadState('networkidle');
    await page.waitForTimeout(200);

    // New button is in #session-tabs with class .session-tab--new and text "+"
    const newBtnText = await page.evaluate(() => {
      const el = document.querySelector('#session-tabs .session-tab--new');
      return el ? el.textContent.trim() : null;
    });
    expect(newBtnText).toBe('+');

    // Start Over remains in the bottom bar (#session-list)
    const resetBtn = page.locator('.session-btn--reset');
    await expect(resetBtn).toBeVisible();
    await expect(resetBtn).toHaveText('Start Over');

    // Both must be semantic button elements
    const newBtnTag = await page.evaluate(() => {
      const el = document.querySelector('#session-tabs .session-tab--new');
      return el ? el.tagName : null;
    });
    expect(newBtnTag).toBe('BUTTON');
    await expect(resetBtn).toHaveJSProperty('tagName', 'BUTTON');
  });

  test('session tabs are keyboard-accessible (role=tab, aria-selected)', async ({ page }) => {
    await page.goto('/');
    await page.locator('#app').waitFor({ state: 'visible', timeout: 10000 });
    await page.waitForLoadState('networkidle');
    await page.waitForTimeout(200);

    const tabRole = await page.evaluate(() => {
      const tab = document.querySelector('#session-tabs .session-tab:not(.session-tab--new)');
      return tab ? tab.getAttribute('role') : null;
    });
    expect(tabRole).toBe('tab');

    const tablistRole = await page.evaluate(() => {
      const el = document.getElementById('session-tabs');
      return el ? el.getAttribute('role') : null;
    });
    expect(tablistRole).toBe('tablist');

    // Reset button in bottom bar must still be focusable
    const resetBtn = page.locator('.session-btn--reset');
    await expect(resetBtn).toBeVisible();
    await resetBtn.focus();
    const focused = page.locator(':focus');
    await expect(focused).toHaveClass(/session-btn--reset/);
  });

  test('clicking session tab loads history via API', async ({ page }) => {
    const sessionId = 'test-session-id';

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
    await page.waitForTimeout(300);

    // Click the session tab (may already have loaded history on auto-activate)
    await page.evaluate((sid) => Sessions.activate(sid), sessionId);
    await page.waitForTimeout(500);
    expect(historyFetched).toBe(true);
  });

  test('history messages render in chat area after tab click', async ({ page }) => {
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
    await page.waitForTimeout(300);

    await page.evaluate((sid) => Sessions.activate(sid), sessionId);
    await page.waitForTimeout(700);

    const chatMessages = page.locator('#chat-messages');
    await expect(chatMessages).toContainText('My question from history');
  });

  test('answered Likert widget renders pre-filled but editable in history', async ({ page }) => {
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
                dimension: 'Meta-Cognitive Awareness',
                sub_dimension: 'Self-Reflective Insight',
                count: 1,
                questions: [{
                  id: qid,
                  text: 'I take time to reflect on my thoughts and reactions.',
                  scale_type: 'agreement_5',
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

    // Editing a history answer upserts via this endpoint — mock it so the
    // re-click below is hermetic (no live-backend auth/rate-limit coupling).
    await page.route('**/api/assessment/responses', route => {
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          saved: true,
          question_id: qid,
          progress: { answered: 1, total: 75 },
        })
      });
    });

    await page.goto('/');
    await page.locator('#app').waitFor({ state: 'visible', timeout: 10000 });
    await page.waitForLoadState('networkidle');
    await page.waitForTimeout(300);

    await page.evaluate((sid) => Sessions.activate(sid), sessionId);
    await page.waitForTimeout(700);

    const likertCard = page.locator('.widget-card').first();
    await expect(likertCard).toBeVisible({ timeout: 3000 });

    // A fully-answered batch auto-collapses on render in history mode
    // (likert-card.js:118), so expand it before interacting. The header only
    // toggles once the batch is complete (likert-card.js:95) — which it is,
    // since the single question is pre-answered.
    await expect(likertCard).toHaveClass(/likert-batch--collapsed/);
    await likertCard.locator('.likert-batch-progress').click();
    await expect(likertCard).not.toHaveClass(/likert-batch--collapsed/);

    // The prior answer is pre-filled: 4th option (Agree, index 3) is selected.
    const options = likertCard.locator('.likert-option');
    const fourthOption = options.nth(3);
    await expect(fourthOption).toHaveClass(/likert-option--selected/);

    // History answers stay EDITABLE (editable-answers feature): options remain
    // enabled so a past answer can be changed on reload — LikertCard never
    // disables them (see likert-card.js:175 "Options stay enabled so the user
    // can correct a mis-click"). Read-only applies only to the chat input of an
    // archived session (chat.js:setReadOnly), not to answered widgets.
    const allOptions = await options.all();
    for (const opt of allOptions) {
      await expect(opt).toBeEnabled();
    }

    // Re-clicking a different option re-selects it, proving the widget is live.
    await options.nth(1).click();
    await expect(options.nth(1)).toHaveClass(/likert-option--selected/);
    await expect(fourthOption).not.toHaveClass(/likert-option--selected/);
  });

  test('page load produces 0 uncaught JavaScript errors', async ({ page }) => {
    await page.goto('/');
    await page.locator('#app').waitFor({ state: 'visible', timeout: 10000 });
    await page.waitForLoadState('networkidle');

    expect(page._jsErrors, `Unexpected JS errors: ${page._jsErrors.join('; ')}`).toHaveLength(0);
  });

});

test.describe('Session Reset (FE-001)', () => {

  test.beforeEach(async ({ page }) => {
    const jsErrors = [];
    page.on('pageerror', err => jsErrors.push(err.message));
    page._jsErrors = jsErrors;

    await bypassAuthWithSession(page);
  });

  test('Start Over button is visible and accessible in bottom bar', async ({ page }) => {
    await page.goto('/');
    await page.locator('#app').waitFor({ state: 'visible', timeout: 10000 });
    await page.waitForLoadState('networkidle');
    await page.waitForTimeout(200);

    const resetBtn = page.locator('.session-btn--reset');
    await expect(resetBtn).toBeVisible();
    await expect(resetBtn).toBeEnabled();
    await expect(resetBtn).toHaveJSProperty('tagName', 'BUTTON');
  });

  test('Start Over shows confirmation dialog before resetting', async ({ page }) => {
    await page.goto('/');
    await page.locator('#app').waitFor({ state: 'visible', timeout: 10000 });
    await page.waitForLoadState('networkidle');
    await page.waitForTimeout(200);

    let dialogShown = false;
    page.on('dialog', async dialog => {
      dialogShown = true;
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
    await page.waitForTimeout(200);

    page.on('dialog', dialog => dialog.accept());

    await page.locator('.session-btn--reset').click();
    await page.waitForTimeout(500);

    expect(resetCalled).toBe(true);
  });

});
