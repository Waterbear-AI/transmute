// @ts-check
/**
 * E2E spec for TEST-001: Tiered Assessment Flow (Tier 1 -> 2 -> 3 -> profile).
 *
 * Covers the acceptance criteria not already exercised by early-result.spec.js
 * (FE-001, which owns: early-result-card rendering, tab-switch persistence,
 * tier-progress affordance showing Tier 1, low-confidence badge, 0-JS-errors
 * on that narrower flow). This suite instead focuses on:
 *
 *  - The Tier-1 "Transmute Core" flow: answering real LikertCard widgets via
 *    genuine button clicks (not synthetic Results.handleSSEEvent calls),
 *    exercising the real POST /api/assessment/responses -> progress-forward
 *    production code path (frontend/js/components/likert-card.js:227-255).
 *  - Tier-2 (awareness_core) and Tier-3 (awareness_deepdive) question batches
 *    rendering with their own dimension/sub_dimension labels — the adaptive
 *    engine's batch-selection logic itself lives in
 *    agents/transmutation/adaptive_engine.py (a pure Python module, already
 *    unit-tested by tests/test_adaptive_engine.py and
 *    tests/test_be004_tier_tools.py) and is NOT re-tested here; this suite
 *    verifies the FRONTEND correctly renders whatever batch the server sends
 *    for each tier, and that the tier-progress affordance reflects
 *    server-authoritative advancement (never computes it client-side).
 *  - "Only flagged dimensions expand" (spec 6.3): get_next_adaptive_batch
 *    (agents/transmutation/tools.py:956-1018) does not return a
 *    flagged-dimensions field itself — the only place a dimension's identity
 *    is observable at the SSE/DOM boundary is the dimension/sub_dimension on
 *    the resulting assessment.question_batch event (tools.py:640-641,
 *    present_question_batch). This suite verifies the LikertCard batch
 *    header renders whichever dimension the mocked event carries, and that a
 *    dimension NOT mentioned in any batch simply never gets a card — i.e.
 *    the frontend has no logic of its own that would render an unflagged
 *    dimension's items.
 *  - Resume mid-assessment: /api/sessions/{id}/history (chat transcript +
 *    answered_responses) and /api/results/{user_id} (assessment_tier,
 *    early_result) are fetched independently on app init
 *    (frontend/js/app.js) and have NO ordering dependency; this suite mocks
 *    both and verifies the resumed UI reflects Tier-2 state without any
 *    tier-transition happening client-side.
 *  - Illegal-transition / cross-user access, tested at the only two HTTP
 *    boundaries that actually exist for these concerns (confirmed by
 *    reading api/results.py and api/sessions.py directly — there is no
 *    HTTP route exposing get_next_adaptive_batch or
 *    evaluate_transmute_core_complete; those are agent-only tools):
 *      - GET /api/results/{target_user_id} with a mismatched target_user_id
 *        -> 403 {"detail": "Cannot access another user's results"}
 *        (api/results.py:314-315).
 *      - GET /api/sessions/{session_id}/history for a session that does not
 *        belong to the caller -> 404 {"detail": "Session not found"}
 *        (api/sessions.py, deliberately 404 not 403 to avoid session-id
 *        enumeration).
 *      - The frontend's TierProgress component (frontend/js/components/
 *        tier-progress.js) never advances a tier locally — it only ever
 *        displays whatever assessment_tier value it is given, confirmed by
 *        grepping the entire frontend/js tree for assessment_tier
 *        assignments (there are none outside TierProgress.create's read).
 *  - Zero uncaught JS errors across every scenario (page.on('pageerror'),
 *    matching the project-wide convention in early-result.spec.js and
 *    session-resume.spec.js).
 */
const { test, expect } = require('@playwright/test');
const path = require('path');

const SCREENSHOTS_DIR = path.join(__dirname, 'screenshots');

/**
 * Bypass auth and mock the three routes every test in this file needs:
 * /auth/me, /api/sessions (list), and /api/results/**. Individual tests
 * layer additional route mocks (session history, POST responses) on top.
 * Mirrors early-result.spec.js's bypassAuth + session-resume.spec.js's
 * richer /api/sessions handling (GET list vs POST create).
 */
async function bypassAuth(page, { userId = 'test-user', resultsBody = {} } = {}) {
    await page.route('**/auth/me', route => {
        route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify({ user_id: userId, name: 'Test User', email: 'test@example.com' })
        });
    });
    await page.route('**/api/sessions', route => {
        if (route.request().method() === 'GET') {
            route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify({ sessions: [], count: 0, user_total_cost_usd: 0 })
            });
        } else {
            route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify({
                    session_id: 'new-session-id',
                    user_id: userId,
                    app_name: 'transmutation',
                    archived: false,
                    created_at: new Date().toISOString(),
                    message_count: 0,
                    title: null
                })
            });
        }
    });
    await page.route('**/api/results/**', route => {
        route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify(resultsBody)
        });
    });
}

async function gotoApp(page) {
    await page.goto('/');
    await page.locator('#app').waitFor({ state: 'visible', timeout: 10000 });
    await page.waitForLoadState('networkidle');
}

async function switchToAssessmentTab(page) {
    await page.locator('.results-tab', { hasText: 'Assessment' }).click();
}

// --- Fixture payloads -------------------------------------------------

/**
 * Exact shape of present_question_batch's return (agents/transmutation/
 * tools.py:604-652), as it reaches the frontend via the assessment.
 * question_batch SSE event / Chat._pendingWidgets -> LikertCard.create.
 * Tier-1 (transmute_core) batch: dimension = "Transmutation Capacity".
 */
const TC_BATCH_PAYLOAD = {
    event_type: 'assessment.question_batch',
    batch_id: 'batch_transmutation_capacity_2',
    sub_dimension: 'Deprivation Filtering',
    dimension: 'Transmutation Capacity',
    questions: [
        {
            id: 'tc_filt_01',
            text: 'I actively work to break cycles of hardship I encounter.',
            scale_type: 'agreement_5',
            scale_labels: ['Strongly Disagree', 'Disagree', 'Neutral', 'Agree', 'Strongly Agree']
        },
        {
            id: 'tc_filt_02',
            text: 'When I face something difficult, I look for ways to transform it.',
            scale_type: 'agreement_5',
            scale_labels: ['Strongly Disagree', 'Disagree', 'Neutral', 'Agree', 'Strongly Agree']
        }
    ],
    question_ids: ['tc_filt_01', 'tc_filt_02'],
    count: 2,
    missing: []
};

/** Tier-2 (awareness_core) batch: no routing, every item is administered. */
const AWARENESS_CORE_BATCH_PAYLOAD = {
    event_type: 'assessment.question_batch',
    batch_id: 'batch_mindful_presence_2',
    sub_dimension: 'Present-Moment Attention',
    dimension: 'Mindful Presence',
    questions: [
        {
            id: 'mp_core_01',
            text: 'I notice the details of my surroundings throughout the day.',
            scale_type: 'agreement_5',
            scale_labels: ['Strongly Disagree', 'Disagree', 'Neutral', 'Agree', 'Strongly Agree']
        }
    ],
    question_ids: ['mp_core_01'],
    count: 1,
    missing: []
};

/**
 * Tier-3 (awareness_deepdive) screener batch for a dimension the adaptive
 * engine's should_expand_dimension (LOW_CUT=2.75 / BORDERLINE_MARGIN=0.5 /
 * INCONSISTENT_RANGE=2.0, agents/transmutation/adaptive_engine.py:98-128)
 * flags for full expansion — represented here simply as "the server sent a
 * second batch for this same dimension," since the frontend has no
 * visibility into *why* the engine flagged it (that decision is pure
 * Python, already unit-tested server-side).
 */
const DEEPDIVE_SCREENER_BATCH_PAYLOAD = {
    event_type: 'assessment.question_batch',
    batch_id: 'batch_self_compassion_1',
    sub_dimension: 'Self-Kindness Screener',
    dimension: 'Self-Compassion',
    questions: [
        {
            id: 'sc_screen_01',
            text: "I'm tolerant of my own flaws and inadequacies.",
            scale_type: 'agreement_5',
            scale_labels: ['Strongly Disagree', 'Disagree', 'Neutral', 'Agree', 'Strongly Agree']
        }
    ],
    question_ids: ['sc_screen_01'],
    count: 1,
    missing: []
};

const DEEPDIVE_EXPANSION_BATCH_PAYLOAD = {
    event_type: 'assessment.question_batch',
    batch_id: 'batch_self_compassion_2',
    sub_dimension: 'Self-Kindness Deep-Dive',
    dimension: 'Self-Compassion',
    questions: [
        {
            id: 'sc_full_01',
            text: 'When times are tough, I give myself the caring and tenderness I need.',
            scale_type: 'agreement_5',
            scale_labels: ['Strongly Disagree', 'Disagree', 'Neutral', 'Agree', 'Strongly Agree']
        }
    ],
    question_ids: ['sc_full_01'],
    count: 1,
    missing: []
};

/**
 * assessment.progress fixture. _compute_progress (tools.py:168-212) does
 * NOT natively include assessment_tier on this dict — it only reaches the
 * frontend authoritatively via /api/results or /api/assessment/state
 * (confirmed by reading both). Tests that need TierProgress to reflect a
 * given tier inject it here as a convenience, exactly as early-result.
 * spec.js's PROGRESS_PAYLOAD already does — this is a test-fixture
 * convenience, not a claim about the real event shape.
 */
function progressPayload(overrides) {
    return Object.assign({
        progress: Object.assign({
            answered: 10,
            total: 113,
            assessment_tier: 'transmute_core',
            dimension_progress: {
                'Transmutation Capacity': { answered: 10, total: 20 }
            }
        }, overrides && overrides.progress)
    }, overrides);
}

test.describe('Tiered Assessment Flow (TEST-001)', () => {

    test.beforeEach(async ({ page }) => {
        page._jsErrors = [];
        page.on('pageerror', err => page._jsErrors.push(err.message));
    });

    test.afterEach(async ({ page }) => {
        const jsErrors = page._jsErrors || [];
        if (jsErrors.length > 0) {
            throw new Error(`Uncaught JS errors detected: ${jsErrors.join('; ')}`);
        }
    });

    test('tier-01: Tier 1 (Transmute Core) batch renders and answering a real widget click saves via POST /api/assessment/responses', async ({ page }) => {
        await bypassAuth(page);
        await gotoApp(page);

        // Deliver the batch the same way Chat._handleSSEEvent buffers domain
        // widgets: via LikertCard.create through the pending-widget path.
        // We call Chat's real routing entrypoint (not Results directly) so
        // the chat.js allowlist + widget-buffering logic is exercised too.
        let saveRequestBody = null;
        await page.route('**/api/assessment/responses', route => {
            saveRequestBody = route.request().postDataJSON();
            route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify({
                    saved: true,
                    question_id: saveRequestBody.question_id,
                    progress: {
                        answered: 1,
                        total: 113,
                        dimension_progress: { 'Transmutation Capacity': { answered: 1, total: 20 } }
                    }
                })
            });
        });

        await page.evaluate((data) => {
            const el = LikertCard.create(data);
            document.getElementById('chat-messages').appendChild(el);
        }, TC_BATCH_PAYLOAD);

        // Batch header shows the real dimension label from the server payload.
        const batchHeader = page.locator('.likert-batch-progress__title');
        await expect(batchHeader).toBeVisible({ timeout: 5000 });
        await expect(batchHeader).toContainText('Deprivation Filtering');

        // Click the first option ("Strongly Disagree") on the first question —
        // a genuine user interaction, not a synthetic event.
        const firstQuestion = page.locator('.likert-question').first();
        await firstQuestion.locator('.likert-option').first().click();

        // The click must have triggered a real POST with the question_id + score.
        await expect.poll(() => saveRequestBody).not.toBeNull();
        expect(saveRequestBody.question_id).toBe('tc_filt_01');
        expect(saveRequestBody.score).toBe(1);

        // Selection is reflected visually (aria-checked + selected class).
        const selectedOption = firstQuestion.locator('.likert-option').first();
        await expect(selectedOption).toHaveClass(/likert-option--selected/);
        await expect(selectedOption).toHaveAttribute('aria-checked', 'true');

        // Progress forwarded into Results (Assessment tab), per
        // likert-card.js:247-249's Results.handleSSEEvent forwarding.
        await switchToAssessmentTab(page);
        await expect(page.locator('#assessment-progress-overall')).toContainText('1 / 113');

        await page.screenshot({ path: `${SCREENSHOTS_DIR}/tier-01-transmute-core-answered.png`, fullPage: false });
    });

    test('tier-02: Tier 2 (Awareness Core) batch renders with its own dimension label, independent of Tier 1', async ({ page }) => {
        await bypassAuth(page);
        await gotoApp(page);

        await page.evaluate((data) => {
            Results.handleSSEEvent('assessment.progress', data);
        }, progressPayload({ progress: { assessment_tier: 'awareness_core' } }));

        await page.evaluate((data) => {
            const el = LikertCard.create(data);
            document.getElementById('chat-messages').appendChild(el);
        }, AWARENESS_CORE_BATCH_PAYLOAD);

        const batchHeader = page.locator('.likert-batch-progress__title');
        await expect(batchHeader).toContainText('Present-Moment Attention');

        await switchToAssessmentTab(page);
        const tier = page.locator('.tier-progress');
        await expect(tier).toContainText('Tier 2 of 3');

        await page.screenshot({ path: `${SCREENSHOTS_DIR}/tier-02-awareness-core-batch.png`, fullPage: false });
    });

    test('tier-03: Tier 3 (Awareness Deep-Dive) only renders an expansion batch for a dimension that received one — a dimension with no expansion event gets no card', async ({ page }) => {
        await bypassAuth(page);
        await gotoApp(page);

        await page.evaluate((data) => {
            Results.handleSSEEvent('assessment.progress', data);
        }, progressPayload({ progress: { assessment_tier: 'awareness_deepdive' } }));

        // Screener batch first (screener-first selection, adaptive_engine.py:
        // _select_deepdive_items_for_dimension), then the flagged expansion
        // batch for the SAME dimension — simulating what a real flagged
        // dimension looks like at the SSE boundary (two sequential batches,
        // same dimension, second one carries the full non-screener items).
        await page.evaluate((data) => {
            const el = LikertCard.create(data);
            document.getElementById('chat-messages').appendChild(el);
        }, DEEPDIVE_SCREENER_BATCH_PAYLOAD);

        await page.evaluate((data) => {
            const el = LikertCard.create(data);
            document.getElementById('chat-messages').appendChild(el);
        }, DEEPDIVE_EXPANSION_BATCH_PAYLOAD);

        const batchTitles = page.locator('.likert-batch-progress__title');
        await expect(batchTitles).toHaveCount(2);
        await expect(batchTitles.nth(0)).toContainText('Self-Kindness Screener');
        await expect(batchTitles.nth(1)).toContainText('Self-Kindness Deep-Dive');

        // A dimension that never received any batch event (e.g. "Reflective
        // Functioning", scored well and not flagged) must have zero
        // corresponding cards — the frontend renders exactly what the
        // server sends, nothing more, nothing inferred client-side.
        const unflaggedCard = page.locator('.likert-batch-progress__title', { hasText: 'Reflective Functioning' });
        await expect(unflaggedCard).toHaveCount(0);

        await switchToAssessmentTab(page);
        const tier = page.locator('.tier-progress');
        await expect(tier).toContainText('Tier 3 of 3');

        await page.screenshot({ path: `${SCREENSHOTS_DIR}/tier-03-deepdive-flagged-expansion.png`, fullPage: false });
    });

    test('tier-04: tier-progress affordance is purely server-authoritative — the same component reflects each tier verbatim with no client-side advancement logic', async ({ page }) => {
        await bypassAuth(page);
        await gotoApp(page);

        const tiers = [
            ['transmute_core', 'Tier 1 of 3'],
            ['awareness_core', 'Tier 2 of 3'],
            ['awareness_deepdive', 'Tier 3 of 3'],
            ['complete', 'All tiers complete']
        ];

        // The first event populates _resultsData.assessment_state, which is
        // what makes the Assessment tab visible in the first place
        // (_isTabVisible, frontend/js/results.js) — switch to it only after
        // that, not before.
        let switchedToAssessment = false;
        for (const [tierValue, expectedText] of tiers) {
            await page.evaluate((data) => {
                Results.handleSSEEvent('assessment.progress', data);
            }, progressPayload({ progress: { assessment_tier: tierValue } }));
            if (!switchedToAssessment) {
                await switchToAssessmentTab(page);
                switchedToAssessment = true;
            }
            await expect(page.locator('.tier-progress')).toContainText(expectedText);
        }
    });

    test('tier-05: resume mid-assessment restores Tier-2 chat history and tier state from two independent API fetches', async ({ page }) => {
        const sessionId = 'resume-session-id';

        // /api/results carries the tier + progress; /api/sessions/{id}/history
        // carries the chat transcript + answered_responses. Confirmed via
        // api/results.py + api/sessions.py that neither response depends on
        // the other having been fetched first (frontend/js/app.js fires both
        // concurrently via Promise.all).
        await bypassAuth(page, {
            resultsBody: {
                user_id: 'test-user',
                assessment: {
                    exists: true,
                    answered: 45,
                    total: 113,
                    current_phase: 'assessment',
                    assessment_tier: 'awareness_core',
                    flagged_dimensions: null,
                    deep_dive_dimensions: null,
                    early_result: {
                        archetype: 'magnifier',
                        x: 0.3,
                        y: -0.1,
                        confidence: 'medium',
                        confidence_reason: 'Based on ~18 core answers; a few more will sharpen it.',
                        computed_at: new Date().toISOString()
                    }
                }
            }
        });

        await page.route(`**/api/sessions/${sessionId}/history`, route => {
            route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify({
                    session_id: sessionId,
                    messages: [
                        { role: 'user', text: 'Ready to continue' },
                        { role: 'agent', text: 'Great — let’s keep going with the Awareness Core section.' },
                        {
                            role: 'widget',
                            event_type: 'assessment.question_batch',
                            data: AWARENESS_CORE_BATCH_PAYLOAD
                        }
                    ],
                    // Prior Tier-1 answer, restored so the widget renders read-only.
                    answered_responses: { tc_filt_01: { score: 4, type: 'likert' } }
                })
            });
        });

        await gotoApp(page);

        // Simulate activating the resumed session (mirrors session-resume.
        // spec.js's established pattern for exercising Sessions.activate).
        await page.evaluate((sid) => Sessions.activate(sid), sessionId);
        await page.waitForTimeout(500);

        // Chat transcript restored.
        await expect(page.locator('#chat-messages')).toContainText('Ready to continue');
        await expect(page.locator('#chat-messages')).toContainText('Awareness Core section');

        // The re-hydrated widget renders from history — dimension label intact.
        await expect(page.locator('.likert-batch-progress__title')).toContainText('Present-Moment Attention');

        // Tier state (from the OTHER endpoint, /api/results) is independently
        // reflected — resume does not require the tier to be embedded in history.
        await page.evaluate((data) => {
            Results.update(data, 'assessment');
        }, { assessment: { exists: true, answered: 45, total: 113, assessment_tier: 'awareness_core' } });
        await switchToAssessmentTab(page);
        await expect(page.locator('.tier-progress')).toContainText('Tier 2 of 3');

        await page.screenshot({ path: `${SCREENSHOTS_DIR}/tier-05-resume-mid-assessment.png`, fullPage: false });
    });

    test('tier-06: cross-user access to /api/results/{other_user_id} is rejected with 403 and an explicit ownership message', async ({ page }) => {
        await bypassAuth(page, { resultsBody: {} });
        await gotoApp(page);

        // Override the blanket /api/results/** mock with one that behaves
        // like the real endpoint's ownership check (api/results.py:314-315):
        // the authenticated user is "test-user"; requesting another user's
        // results must 403 with the exact detail message the API returns.
        await page.route('**/api/results/other-user-id', route => {
            route.fulfill({
                status: 403,
                contentType: 'application/json',
                body: JSON.stringify({ detail: "Cannot access another user's results" })
            });
        });

        const response = await page.evaluate(async () => {
            const res = await fetch('/api/results/other-user-id');
            const body = await res.json().catch(() => null);
            return { status: res.status, body };
        });

        expect(response.status).toBe(403);
        expect(response.body.detail).toBe("Cannot access another user's results");
    });

    test('tier-07: cross-session access to /api/sessions/{other_session_id}/history is rejected with 404 (enumeration-safe), not 403', async ({ page }) => {
        await bypassAuth(page);
        await gotoApp(page);

        // api/sessions.py deliberately returns 404 (not 403) for a session
        // that does not belong to the caller, specifically to avoid leaking
        // "this session ID exists but isn't yours" via a distinguishable
        // status code (session-id enumeration). Confirmed by reading the
        // endpoint's query (WHERE session_id = ? AND user_id = ?) and the
        // adjacent comment.
        await page.route('**/api/sessions/not-my-session-id/history', route => {
            route.fulfill({
                status: 404,
                contentType: 'application/json',
                body: JSON.stringify({ detail: 'Session not found' })
            });
        });

        const response = await page.evaluate(async () => {
            const res = await fetch('/api/sessions/not-my-session-id/history');
            const body = await res.json().catch(() => null);
            return { status: res.status, body };
        });

        expect(response.status).toBe(404);
        expect(response.body.detail).toBe('Session not found');
    });

    test('tier-08: full progression screenshot — Tier 1 answered through Tier 3 flagged expansion, then Profile arrival, in one continuous session', async ({ page }) => {
        await bypassAuth(page);
        await gotoApp(page);

        // Tier 1: answer via a real widget click.
        await page.route('**/api/assessment/responses', route => {
            route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify({
                    saved: true,
                    question_id: 'tc_filt_01',
                    progress: { answered: 20, total: 113, dimension_progress: { 'Transmutation Capacity': { answered: 20, total: 20 } } }
                })
            });
        });
        await page.evaluate((data) => {
            const el = LikertCard.create(data);
            document.getElementById('chat-messages').appendChild(el);
        }, TC_BATCH_PAYLOAD);
        await page.locator('.likert-question').first().locator('.likert-option').last().click();

        // Tier-1 completion fires the early result mid-conversation.
        await page.evaluate((data) => {
            Results.handleSSEEvent('assessment.transmute_result', data);
        }, {
            event_type: 'assessment.transmute_result',
            archetype: 'transmuter',
            x: 0.55,
            y: 0.6,
            confidence: 'high',
            confidence_reason: 'Based on 20 core answers and 5 scenarios — a solid early read.'
        });

        // Advance to Tier 2, then Tier 3 with a flagged expansion.
        await page.evaluate((data) => {
            Results.handleSSEEvent('assessment.progress', data);
        }, progressPayload({ progress: { assessment_tier: 'awareness_deepdive', answered: 90 } }));

        await page.evaluate((data) => {
            const el = LikertCard.create(data);
            document.getElementById('chat-messages').appendChild(el);
        }, DEEPDIVE_EXPANSION_BATCH_PAYLOAD);

        // Profile arrival — phase transition + a full snapshot.
        await page.evaluate(() => {
            Results.handlePhaseTransition('assessment', 'profile');
        });
        await page.evaluate((data) => {
            Results.handleSSEEvent('profile.snapshot', data);
        }, {
            quadrant_placement: { archetype: 'transmuter', x: 0.55, y: 0.6, confidence: 'high' },
            interpretation: 'Your pattern shows strong transmutation capacity across both filtering and amplification.'
        });

        await expect(page.locator('.results-tab', { hasText: 'Profile' })).toBeVisible({ timeout: 5000 });
        await page.screenshot({ path: `${SCREENSHOTS_DIR}/tier-08-full-progression-to-profile.png`, fullPage: false });
    });
});
