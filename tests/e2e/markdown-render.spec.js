// @ts-check
/**
 * Regression e2e for agent-message markdown rendering.
 * Locks in that "## " headings and ">" blockquotes (with nested bold + list)
 * render as real elements rather than literal text. Drives the public
 * Chat.renderHistory path, which renders agent text through _markdownToHTML.
 */
const { test, expect } = require('@playwright/test');

async function bypassAuth(page) {
    await page.route('**/auth/me', route => route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ user_id: 'test-user', name: 'Test User', email: 'test@example.com', current_phase: 'education' }),
    }));
    await page.route('**/api/sessions', route => route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ sessions: [{ session_id: 's1', user_id: 'test-user', message_count: 0 }], count: 1, user_total_cost_usd: 0 }),
    }));
    await page.route('**/api/sessions/**/history', route => route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ session_id: 's1', messages: [], answered_responses: {} }),
    }));
    await page.route('**/api/results/**', route => route.fulfill({
        status: 200, contentType: 'application/json', body: JSON.stringify({}),
    }));
}

test.describe('Agent markdown rendering', () => {
    test.beforeEach(async ({ page }) => {
        page._jsErrors = [];
        page.on('pageerror', err => page._jsErrors.push(err.message));
        await bypassAuth(page);
        await page.goto('/');
        await page.locator('#app').waitFor({ state: 'visible', timeout: 10000 });
    });

    test.afterEach(async ({ page }) => {
        const errs = page._jsErrors || [];
        if (errs.length > 0) throw new Error('Uncaught JS errors: ' + errs.join('; '));
    });

    test('md-01: ## heading and > blockquote render as elements, not literal text', async ({ page }) => {
        await page.evaluate(() => {
            Chat.renderHistory([
                { role: 'agent', text: '## Emotional Awareness\n\n**Lead** sentence.\n\n> **Which best describes why?**\n>\n> - A) memorize vocabulary\n> - B) real-time visibility' },
            ]);
        });

        const messages = page.locator('#chat-messages');
        // Heading rendered as <h2> with the right text (not literal "## ...").
        await expect(messages.locator('h2')).toHaveText('Emotional Awareness', { timeout: 5000 });
        // Blockquote rendered, containing the bolded question and a list.
        const bq = messages.locator('blockquote');
        await expect(bq).toHaveCount(1);
        await expect(bq.locator('strong')).toContainText('Which best describes why?');
        await expect(bq.locator('ul li')).toHaveCount(2);
        // Literal markers must NOT appear as text.
        await expect(messages).not.toContainText('## Emotional');
        await expect(messages).not.toContainText('> -');
    });
});
