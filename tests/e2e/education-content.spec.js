// @ts-check
/**
 * E2E spec for the education.content event (FE-001).
 *
 * present_education_content delivers a teaching explanation as one complete
 * block via the education.content SSE event (live) and as a stored
 * function_response event on history replay. Both paths must render the
 * content identically: a fresh agent-styled chat bubble containing sanitized
 * markdown (via the shared Markdown module), with any subsequent streamed
 * text opening its own new bubble rather than appending to it.
 */
const { test, expect } = require('@playwright/test');
const path = require('path');

const SCREENSHOTS_DIR = path.join(__dirname, 'screenshots');

async function bypassAuth(page) {
    await page.route('**/auth/me', route => route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
            user_id: 'test-user',
            name: 'Test User',
            email: 'test@example.com',
            current_phase: 'education',
        }),
    }));
    await page.route('**/api/sessions', route => route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
            sessions: [{ session_id: 's1', user_id: 'test-user', message_count: 0 }],
            count: 1,
            user_total_cost_usd: 0,
        }),
    }));
    await page.route('**/api/sessions/**/history', route => route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ session_id: 's1', messages: [], answered_responses: {} }),
    }));
    await page.route('**/api/results/**', route => route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({}),
    }));
}

/** Build a text/event-stream body from [{event, data}] pairs. */
function sseBody(events) {
    return events.map(({ event, data }) =>
        `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`
    ).join('');
}

test.describe('education.content event rendering', () => {
    test.beforeEach(async ({ page }) => {
        page._jsErrors = [];
        page.on('pageerror', err => page._jsErrors.push(err.message));

        await bypassAuth(page);
        await page.goto('/');
        await page.locator('#app').waitFor({ state: 'visible', timeout: 10000 });
        await page.waitForLoadState('networkidle');

        await page.evaluate(() => {
            if (typeof App !== 'undefined') {
                App._testSessionId = 's1';
                const origGet = App.getCurrentSessionId;
                App.getCurrentSessionId = () => App._testSessionId || (origGet && origGet.call(App));
            }
        });
    });

    test.afterEach(async ({ page }) => {
        const errs = page._jsErrors || [];
        if (errs.length > 0) throw new Error('Uncaught JS errors: ' + errs.join('; '));
    });

    // ── Live SSE render ─────────────────────────────────────────────────────

    test('edu-content-01: live education.content SSE event renders a fresh agent message', async ({ page }) => {
        await page.route('**/api/chat/**', route => route.fulfill({
            status: 200,
            contentType: 'text/event-stream',
            body: sseBody([
                {
                    event: 'education.content',
                    data: {
                        status: 'success',
                        event_type: 'education.content',
                        dimension: 'Emotional Awareness & Regulation',
                        category: 'what_this_means',
                        category_label: 'What This Means',
                        content: '**Emotional Awareness** means noticing your feelings as they arise.',
                    },
                },
            ]),
        }));

        await page.evaluate(() => Chat.sendMessage('s1', 'teach me'));

        const messages = page.locator('#chat-messages');
        const agentMsgs = messages.locator('.chat-msg--agent');
        await expect(agentMsgs).toHaveCount(1, { timeout: 5000 });
        await expect(agentMsgs.locator('strong')).toHaveText('Emotional Awareness');
        await expect(agentMsgs).toContainText('means noticing your feelings as they arise.');

        await page.screenshot({ path: `${SCREENSHOTS_DIR}/edu-content-01-live-render.png` });
    });

    test('edu-content-02: a subsequent streamed chunk opens a new bubble, not appending to education.content', async ({ page }) => {
        await page.route('**/api/chat/**', route => route.fulfill({
            status: 200,
            contentType: 'text/event-stream',
            body: sseBody([
                {
                    event: 'education.content',
                    data: {
                        event_type: 'education.content',
                        dimension: 'Emotional Awareness & Regulation',
                        category: 'what_this_means',
                        content: 'First captured teaching block.',
                    },
                },
                { event: 'agent.message.chunk', data: { text: 'Now, a comprehension check:' } },
                { event: 'agent.message.complete', data: { text: 'Now, a comprehension check:' } },
            ]),
        }));

        await page.evaluate(() => Chat.sendMessage('s1', 'teach me'));

        const messages = page.locator('#chat-messages');
        await expect(messages.locator('.chat-msg--agent')).toHaveCount(2, { timeout: 5000 });
        await expect(messages.locator('.chat-msg--agent').nth(0)).toContainText('First captured teaching block.');
        await expect(messages.locator('.chat-msg--agent').nth(1)).toContainText('Now, a comprehension check:');
    });

    // ── History replay ──────────────────────────────────────────────────────

    test('edu-content-03: history replay renders education.content identically to live', async ({ page }) => {
        await page.evaluate(() => {
            Chat.renderHistory([
                {
                    role: 'widget',
                    event_type: 'education.content',
                    data: {
                        dimension: 'Emotional Awareness & Regulation',
                        category: 'what_this_means',
                        content: '**Emotional Awareness** means noticing your feelings as they arise.',
                    },
                },
            ]);
        });

        const messages = page.locator('#chat-messages');
        const agentMsgs = messages.locator('.chat-msg--agent');
        await expect(agentMsgs).toHaveCount(1, { timeout: 5000 });
        await expect(agentMsgs.locator('strong')).toHaveText('Emotional Awareness');
        await expect(agentMsgs).toContainText('means noticing your feelings as they arise.');

        await page.screenshot({ path: `${SCREENSHOTS_DIR}/edu-content-03-history-replay.png` });
    });

    test('edu-content-04: captured content renders inert on XSS payload', async ({ page }) => {
        await page.evaluate(() => {
            Chat.renderHistory([
                {
                    role: 'widget',
                    event_type: 'education.content',
                    data: {
                        dimension: 'Emotional Awareness & Regulation',
                        category: 'what_this_means',
                        content: 'Some text <img src=x onerror="window._xssFired = true">',
                    },
                },
            ]);
        });

        const messages = page.locator('#chat-messages');
        await expect(messages.locator('.chat-msg--agent')).toHaveCount(1, { timeout: 5000 });
        // The <img onerror> must not execute and must not survive as an <img> tag
        // (img is not in Sanitize's allowlist).
        await expect(messages.locator('img')).toHaveCount(0);
        const xssFired = await page.evaluate(() => window._xssFired);
        expect(xssFired).toBeUndefined();
    });
});
