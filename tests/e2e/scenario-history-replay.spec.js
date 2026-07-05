// @ts-check
/**
 * E2E spec for FE-003: wire scenario_responses into history replay.
 *
 * Verifies the full plumbing added in FE-003 (spec.md Required Implementation):
 *  - sessions.js threads data.scenario_responses from the /history API
 *    response into Chat.renderHistory as a third argument
 *  - Chat.renderHistory(messages, answeredResponses, scenarioResponses)
 *    forwards scenarioResponses through _renderHistoryWidget
 *  - for an assessment.scenario widget message, ScenarioCard.create is
 *    called with scenarioResponses[data.scenario_id]?.choice as the prior
 *    selection, so a reloaded scenario shows the prior pick highlighted
 *    and is still editable
 *  - a scenario with no entry in scenario_responses renders unanswered
 *    (no option pre-selected) -- optional chaining must not throw
 *  - callers that omit scenarioResponses entirely (older call sites/tests)
 *    do not break -- it defaults to {}
 *  - zero uncaught JS errors
 */
const { test, expect } = require('@playwright/test');
const path = require('path');

const SCREENSHOTS_DIR = path.join(__dirname, 'screenshots');

const SCENARIO_EVENT_1 = {
    role: 'widget',
    event_type: 'assessment.scenario',
    data: {
        scenario_id: 'sc_belong_01',
        dimension: 'Belonging',
        narrative: 'A friend asks you for help moving on short notice. What do you do?',
        choices: [
            { key: 'a', text: 'Drop everything and help them.' },
            { key: 'b', text: 'Explain you cannot today but offer another time.' },
            { key: 'c', text: 'Say no without further explanation.' },
        ],
    },
};

const SCENARIO_EVENT_2_UNANSWERED = {
    role: 'widget',
    event_type: 'assessment.scenario',
    data: {
        scenario_id: 'sc_belong_02',
        dimension: 'Belonging',
        narrative: 'A coworker takes credit for your idea in a meeting. What do you do?',
        choices: [
            { key: 'a', text: 'Speak up immediately in the meeting.' },
            { key: 'b', text: 'Address it privately afterward.' },
            { key: 'c', text: 'Let it go.' },
        ],
    },
};

async function bypassAuth(page) {
    await page.route('**/auth/me', route => route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ user_id: 'test-user', name: 'Test User', email: 'test@example.com' }),
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

test.describe('Scenario history replay wiring (FE-003)', () => {

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

    test('rep-01: Chat.renderHistory threads scenario_responses into ScenarioCard.create as selectedChoice', async ({ page }) => {
        await page.evaluate(([evt1, scenarioResponses]) => {
            Chat.renderHistory([evt1], {}, scenarioResponses);
        }, [SCENARIO_EVENT_1, { sc_belong_01: { choice: 'b', answered_at: '2026-07-01T00:00:00' } }]);

        const choiceB = page.locator('.scenario-choice').nth(1);
        await expect(choiceB).toHaveClass(/scenario-choice--selected/);
        await expect(choiceB).toHaveAttribute('aria-pressed', 'true');

        // Still editable -- all options remain enabled/clickable.
        await expect(page.locator('.scenario-choice').nth(0)).toBeEnabled();
        await expect(page.locator('.scenario-choice').nth(2)).toBeEnabled();

        await page.locator('.scenario-choices').scrollIntoViewIfNeeded();
        await page.screenshot({ path: `${SCREENSHOTS_DIR}/rep-01-prior-choice-replayed.png` });
    });

    test('rep-02: a scenario with no entry in scenario_responses renders unanswered (no throw)', async ({ page }) => {
        await page.evaluate(([evt2]) => {
            Chat.renderHistory([evt2], {}, { some_other_scenario: { choice: 'a' } });
        }, [SCENARIO_EVENT_2_UNANSWERED]);

        const choices = page.locator('.scenario-choice');
        await expect(choices).toHaveCount(3);
        for (let i = 0; i < 3; i++) {
            await expect(choices.nth(i)).not.toHaveClass(/scenario-choice--selected/);
            await expect(choices.nth(i)).toHaveAttribute('aria-pressed', 'false');
        }
    });

    test('rep-03: multiple replayed scenarios each get their own prior choice', async ({ page }) => {
        await page.evaluate(([evt1, evt2, scenarioResponses]) => {
            Chat.renderHistory([evt1, evt2], {}, scenarioResponses);
        }, [
            SCENARIO_EVENT_1,
            SCENARIO_EVENT_2_UNANSWERED,
            {
                sc_belong_01: { choice: 'a' },
                sc_belong_02: { choice: 'c' },
            },
        ]);

        const cards = page.locator('.widget-card');
        await expect(cards).toHaveCount(2);

        // First card: choice 'a' selected.
        await expect(cards.nth(0).locator('.scenario-choice').nth(0)).toHaveClass(/scenario-choice--selected/);
        // Second card: choice 'c' selected.
        await expect(cards.nth(1).locator('.scenario-choice').nth(2)).toHaveClass(/scenario-choice--selected/);
    });

    test('rep-04: renderHistory called with no scenarioResponses arg (legacy callers) does not throw', async ({ page }) => {
        await page.evaluate(([evt1]) => {
            // Omit the third argument entirely.
            Chat.renderHistory([evt1], {});
        }, [SCENARIO_EVENT_1]);

        const choices = page.locator('.scenario-choice');
        await expect(choices).toHaveCount(3);
        // Nothing pre-selected -- scenarioAnswers defaulted to {}.
        for (let i = 0; i < 3; i++) {
            await expect(choices.nth(i)).not.toHaveClass(/scenario-choice--selected/);
        }
    });

    test('rep-05: sessions.js threads data.scenario_responses from /history into Chat.renderHistory', async ({ page }) => {
        // Full integration through Sessions.activate(): mock /api/sessions/**/history
        // to return scenario_responses and confirm the ScenarioCard renders the
        // prior choice -- proves the sessions.js -> chat.js wire, not just chat.js
        // in isolation (rep-01..04 call Chat.renderHistory directly).
        await page.route('**/api/sessions/**/history', route => route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify({
                session_id: 's1',
                messages: [SCENARIO_EVENT_1],
                answered_responses: {},
                scenario_responses: { sc_belong_01: { choice: 'c' } },
            }),
        }));

        await page.evaluate(() => {
            Sessions.activate('s1');
        });
        await page.waitForTimeout(300);

        const choiceC = page.locator('.scenario-choice').nth(2);
        await expect(choiceC).toHaveClass(/scenario-choice--selected/, { timeout: 5000 });
        await expect(choiceC).toHaveAttribute('aria-pressed', 'true');
    });

    test('rep-06: 0 JS errors across the full replay + edit flow', async ({ page }) => {
        await page.evaluate(([evt1, evt2, scenarioResponses]) => {
            Chat.renderHistory([evt1, evt2], {}, scenarioResponses);
        }, [
            SCENARIO_EVENT_1,
            SCENARIO_EVENT_2_UNANSWERED,
            { sc_belong_01: { choice: 'b' } },
        ]);
        await page.waitForTimeout(200);

        // afterEach asserts 0 pageerror events -- this test just exercises the flow.
        await expect(page.locator('.widget-card')).toHaveCount(2);
    });
});
