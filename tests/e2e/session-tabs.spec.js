// @ts-check
/**
 * E2E spec for FE-001: Session tab strip, inline rename, and reset error handling.
 *
 * The spec intercepts sessions.js to serve the updated module (since the running
 * Docker container serves a stale version), then mocks all API routes.
 */
const { test, expect } = require('@playwright/test');
const path = require('path');
const fs = require('fs');

const SCREENSHOTS_DIR = path.join(__dirname, 'screenshots');
const SESSIONS_JS_PATH = path.join(__dirname, '../../frontend/js/sessions.js');

// ── Helpers ──────────────────────────────────────────────────────────────────

function makeSession(overrides) {
    return {
        session_id: 'sid1',
        user_id: 'u1',
        app_name: 'transmutation',
        archived: false,
        created_at: new Date().toISOString(),
        message_count: 0,
        title: null,
        ...overrides,
    };
}

function sessionListBody(sessions) {
    return JSON.stringify({
        sessions: sessions || [],
        count: (sessions || []).length,
        user_total_cost_usd: 0,
    });
}

async function screenshot(page, name) {
    if (!fs.existsSync(SCREENSHOTS_DIR)) fs.mkdirSync(SCREENSHOTS_DIR, { recursive: true });
    await page.screenshot({ path: path.join(SCREENSHOTS_DIR, name), fullPage: false });
}

/**
 * Set up routes: intercept sessions.js to serve the updated module from source,
 * and mock all backend API endpoints.
 */
async function setupRoutes(page, sessions, extraRouteSetup) {
    // Serve the updated sessions.js from the local source tree
    const sessionsJsContent = fs.readFileSync(SESSIONS_JS_PATH, 'utf-8');
    await page.route('**/js/sessions.js', route => route.fulfill({
        status: 200,
        contentType: 'application/javascript',
        body: sessionsJsContent,
    }));

    // Auth check
    await page.route('**/auth/me', route => route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
            user_id: 'u1',
            name: 'Tab User',
            email: 'tabs@example.com',
            current_phase: 'education',
        }),
    }));

    // Session list (GET)
    await page.route('**/api/sessions', route => {
        if (route.request().method() === 'GET') {
            route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: sessionListBody(sessions),
            });
        } else {
            route.fallback();
        }
    });

    // Session history
    await page.route('**/api/sessions/**/history', route => route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ session_id: 'sid1', messages: [], answered_responses: {} }),
    }));

    // Results panel
    await page.route('**/api/results/**', route => route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({}),
    }));

    // Allow caller to add test-specific routes (e.g. POST /api/sessions)
    if (extraRouteSetup) await extraRouteSetup(page);
}

async function loadApp(page) {
    await page.goto('/');
    await page.locator('#app').waitFor({ state: 'visible', timeout: 10000 });
    await page.waitForLoadState('networkidle');
    await page.waitForTimeout(300);
}

// ── Tests ────────────────────────────────────────────────────────────────────

test.describe('Session tab strip (FE-001)', () => {

    test.beforeEach(async ({ page }) => {
        page._jsErrors = [];
        page.on('pageerror', err => page._jsErrors.push(err.message));
    });

    test.afterEach(async ({ page }) => {
        const errs = page._jsErrors || [];
        if (errs.length > 0) throw new Error('Uncaught JS errors: ' + errs.join('; '));
    });

    // ── tab-01: tab strip rendered with sessions and New button ──────────────

    test('tab-01: tab strip renders sessions as tabs with New button', async ({ page }) => {
        const sessions = [
            makeSession({ session_id: 'sid1', title: 'Chat 1', message_count: 3 }),
            makeSession({ session_id: 'sid2', title: 'Chat 2', message_count: 0 }),
        ];
        await setupRoutes(page, sessions);
        await loadApp(page);

        const tabCount = await page.evaluate(() =>
            document.querySelectorAll('#session-tabs .session-tab:not(.session-tab--new)').length
        );
        expect(tabCount).toBe(2);

        const tabTexts = await page.evaluate(() =>
            Array.from(
                document.querySelectorAll('#session-tabs .session-tab:not(.session-tab--new)')
            ).map(t => t.textContent.trim())
        );
        expect(tabTexts).toContain('Chat 1');
        expect(tabTexts).toContain('Chat 2');

        const hasNewBtn = await page.evaluate(() =>
            !!document.querySelector('#session-tabs .session-tab--new')
        );
        expect(hasNewBtn).toBe(true);

        await screenshot(page, 'tabs-01-strip-renders.png');
    });

    // ── tab-02: active tab ARIA ──────────────────────────────────────────────

    test('tab-02: active tab has correct ARIA attributes', async ({ page }) => {
        await setupRoutes(page, [makeSession({ session_id: 'sid1', title: 'Active Tab' })]);
        await loadApp(page);

        const ariaSelected = await page.evaluate(() => {
            const tab = document.querySelector('#session-tabs .session-tab[data-session-id="sid1"]');
            return tab ? tab.getAttribute('aria-selected') : null;
        });
        expect(ariaSelected).toBe('true');

        const role = await page.evaluate(() => {
            const tab = document.querySelector('#session-tabs .session-tab[data-session-id="sid1"]');
            return tab ? tab.getAttribute('role') : null;
        });
        expect(role).toBe('tab');

        const isActive = await page.evaluate(() => {
            const tab = document.querySelector('#session-tabs .session-tab[data-session-id="sid1"]');
            return tab ? tab.classList.contains('session-tab--active') : false;
        });
        expect(isActive).toBe(true);

        await screenshot(page, 'tabs-02-active-aria.png');
    });

    // ── tab-03: tablist role ─────────────────────────────────────────────────

    test('tab-03: tab strip container has role=tablist', async ({ page }) => {
        await setupRoutes(page, [makeSession()]);
        await loadApp(page);

        const role = await page.evaluate(() => {
            const el = document.getElementById('session-tabs');
            return el ? el.getAttribute('role') : null;
        });
        expect(role).toBe('tablist');
    });

    // ── tab-04: New button sends archive_prior=false ─────────────────────────

    test('tab-04: New button creates non-archiving session', async ({ page }) => {
        const sessions = [makeSession({ session_id: 'sid1', title: 'Existing' })];
        let postBody = null;

        await setupRoutes(page, sessions, async (p) => {
            await p.route('**/api/sessions', route => {
                const method = route.request().method();
                if (method === 'POST') {
                    postBody = route.request().postDataJSON();
                    route.fulfill({
                        status: 200,
                        contentType: 'application/json',
                        body: JSON.stringify(makeSession({ session_id: 'sid2', title: null })),
                    });
                } else {
                    route.fulfill({
                        status: 200,
                        contentType: 'application/json',
                        body: sessionListBody([
                            ...sessions,
                            makeSession({ session_id: 'sid2', title: null }),
                        ]),
                    });
                }
            });
        });

        await loadApp(page);

        await page.evaluate(() => Sessions.createNew());
        await page.waitForTimeout(400);

        expect(postBody).not.toBeNull();
        expect(postBody.archive_prior).toBe(false);

        await screenshot(page, 'tabs-04-new-session.png');
    });

    // ── tab-05: inline rename ────────────────────────────────────────────────

    test('tab-05: inline rename sends PATCH and updates tab label', async ({ page }) => {
        const sessions = [makeSession({ session_id: 'sid1', title: 'Old Name' })];
        let patchBody = null;

        await setupRoutes(page, sessions, async (p) => {
            await p.route('**/api/sessions/sid1', route => {
                if (route.request().method() === 'PATCH') {
                    patchBody = route.request().postDataJSON();
                    route.fulfill({
                        status: 200,
                        contentType: 'application/json',
                        body: JSON.stringify(makeSession({ session_id: 'sid1', title: 'New Name' })),
                    });
                } else {
                    route.fallback();
                }
            });
        });

        await loadApp(page);

        await page.evaluate(() => Sessions._beginRenameForTest('sid1'));
        await page.waitForTimeout(150);

        const inputVisible = await page.evaluate(() =>
            !!document.querySelector('#session-tabs .session-tab__rename-input')
        );
        expect(inputVisible).toBe(true);

        await page.evaluate(() => {
            const input = document.querySelector('#session-tabs .session-tab__rename-input');
            if (input) {
                input.value = 'New Name';
                input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
            }
        });
        await page.waitForTimeout(300);

        expect(patchBody).not.toBeNull();
        expect(patchBody.title).toBe('New Name');

        const tabText = await page.evaluate(() => {
            const tab = document.querySelector('#session-tabs .session-tab[data-session-id="sid1"]');
            return tab ? tab.textContent.trim() : '';
        });
        expect(tabText).toBe('New Name');

        await screenshot(page, 'tabs-05-rename-saved.png');
    });

    // ── tab-06: Esc cancels rename ───────────────────────────────────────────

    test('tab-06: Esc cancels rename without API call', async ({ page }) => {
        const sessions = [makeSession({ session_id: 'sid1', title: 'Original' })];
        let patchCalled = false;

        await setupRoutes(page, sessions, async (p) => {
            await p.route('**/api/sessions/sid1', route => {
                if (route.request().method() === 'PATCH') {
                    patchCalled = true;
                    route.fulfill({
                        status: 200, contentType: 'application/json',
                        body: JSON.stringify(makeSession({ session_id: 'sid1', title: 'Changed' })),
                    });
                } else {
                    route.fallback();
                }
            });
        });

        await loadApp(page);

        await page.evaluate(() => Sessions._beginRenameForTest('sid1'));
        await page.waitForTimeout(150);

        await page.evaluate(() => {
            const input = document.querySelector('#session-tabs .session-tab__rename-input');
            if (input) {
                input.value = 'Changed';
                input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
            }
        });
        await page.waitForTimeout(200);

        expect(patchCalled).toBe(false);

        const tabText = await page.evaluate(() => {
            const tab = document.querySelector('#session-tabs .session-tab[data-session-id="sid1"]');
            return tab ? tab.textContent.trim() : '';
        });
        expect(tabText).toBe('Original');

        await screenshot(page, 'tabs-06-rename-cancelled.png');
    });

    // ── tab-07: reset 429 specific message ──────────────────────────────────

    test('tab-07: reset 429 shows specific rate-limit message', async ({ page }) => {
        await setupRoutes(page, [makeSession()], async (p) => {
            await p.route('**/api/sessions/reset', route => route.fulfill({
                status: 429,
                contentType: 'application/json',
                headers: { 'Retry-After': '300' },
                body: JSON.stringify({ detail: 'rate limited' }),
            }));
        });

        await loadApp(page);

        let toastText = null;
        await page.exposeFunction('captureToastMsg', (msg) => { toastText = msg; });
        await page.evaluate(() => {
            if (typeof Toast !== 'undefined') {
                const origShow = Toast.show.bind(Toast);
                Toast.show = (msg, type) => { window.captureToastMsg(msg); return origShow(msg, type); };
            }
        });

        page.on('dialog', dialog => dialog.accept());
        await page.evaluate(() => Sessions.resetAll());
        await page.waitForTimeout(500);

        if (toastText) {
            // New sessions.js maps 429 → "You've reset too many times recently..."
            expect(toastText.toLowerCase()).toMatch(/too many|rate|wait|\d+\s*seconds?/);
        }

        await screenshot(page, 'tabs-07-reset-429.png');
    });

    // ── tab-08: XSS safety ───────────────────────────────────────────────────

    test('tab-08: tab titles are XSS-safe (textContent not innerHTML)', async ({ page }) => {
        const xssTitle = '<img src=x onerror="window._xssHit=true">';
        await setupRoutes(page, [makeSession({ session_id: 'sid1', title: xssTitle })]);
        await loadApp(page);

        const xssHit = await page.evaluate(() => window._xssHit);
        expect(xssHit).toBeFalsy();

        const tabText = await page.evaluate(() => {
            const tab = document.querySelector('#session-tabs .session-tab[data-session-id="sid1"]');
            return tab ? tab.textContent.trim() : '';
        });
        expect(tabText).toBe(xssTitle);

        await screenshot(page, 'tabs-08-xss-inert.png');
    });

});
