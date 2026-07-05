/* Transmutation Engine - ScenarioCard Component */
'use strict';

const ScenarioCard = (() => {

    /**
     * Create a ScenarioCard from an assessment.scenario SSE event.
     * data: {scenario_id, dimension, narrative, choices: [{key, text}], has_follow_up}
     * selectedChoice: optional prior choice key (from history replay) — when
     * present, prefills/highlights that option on render and suppresses the
     * agent-advance signal (mirrors LikertCard's answers/batchNotified parity:
     * likert-card.js:11,76).
     */
    function create(data, selectedChoice = null) {
        const card = document.createElement('div');
        card.className = 'widget-card';
        card.setAttribute('role', 'region');
        card.setAttribute('aria-label', 'Scenario: ' + (data.dimension || ''));

        const narrative = document.createElement('div');
        narrative.className = 'scenario-narrative';
        Sanitize.setText(narrative, data.narrative);
        card.appendChild(narrative);

        const choicesEl = document.createElement('div');
        choicesEl.className = 'scenario-choices';

        // Fire the agent-advance signal at most once per scenario. Initialized
        // to true when the scenario renders already answered (history replay
        // with a prior selectedChoice) so editing a past pick re-saves +
        // re-scores without re-signalling completion — mirrors LikertCard's
        // batchNotified = isComplete() (likert-card.js:76).
        let notified = (selectedChoice != null);

        for (const choice of data.choices) {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'scenario-choice';
            // Selected state must be conveyed via more than color alone
            // (frontend-accessibility R7) — aria-pressed carries it to
            // assistive tech; the --selected class carries the visual style.
            btn.setAttribute('aria-pressed', 'false');

            const keySpan = document.createElement('span');
            keySpan.className = 'scenario-choice__key';
            Sanitize.setText(keySpan, choice.key);
            btn.appendChild(keySpan);

            btn.appendChild(Sanitize.textNode(choice.text));

            // Prefill/highlight the prior choice (history replay).
            if (selectedChoice != null && choice.key === selectedChoice) {
                btn.classList.add('scenario-choice--selected');
                btn.setAttribute('aria-pressed', 'true');
            }

            // Keyboard navigation: arrow keys between choices
            btn.addEventListener('keydown', (e) => {
                const btns = Array.from(choicesEl.querySelectorAll('.scenario-choice:not(:disabled)'));
                const idx = btns.indexOf(btn);
                let target = null;

                if (e.key === 'ArrowRight' || e.key === 'ArrowDown') {
                    target = btns[(idx + 1) % btns.length];
                } else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
                    target = btns[(idx - 1 + btns.length) % btns.length];
                } else if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    btn.click();
                    return;
                }

                if (target) {
                    e.preventDefault();
                    btns.forEach(b => b.setAttribute('tabindex', '-1'));
                    target.setAttribute('tabindex', '0');
                    target.focus();
                }
            });

            btn.addEventListener('click', async () => {
                if (btn.disabled) return;

                // Select this choice (re-selectable: clear any prior selection so
                // the user can correct a mis-click, live or on a reloaded/history
                // -replayed scenario). The API upserts the response.
                choicesEl.querySelectorAll('.scenario-choice').forEach(b => {
                    b.classList.remove('scenario-choice--selected');
                    b.setAttribute('aria-pressed', 'false');
                });
                btn.classList.add('scenario-choice--selected');
                btn.setAttribute('aria-pressed', 'true');

                // POST to API
                try {
                    const res = await fetch('/api/assessment/responses', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            question_id: data.scenario_id,
                            type: 'scenario',
                            choice_key: choice.key
                        })
                    });
                    if (!res.ok) {
                        console.error('[ScenarioCard] Save failed:', res.status);
                        return;
                    }
                    // Refresh the results panel when this edit was transmute-
                    // relevant and regenerated the cached early_result (scenarios
                    // are always transmute-relevant, per BE-001). Mirrors
                    // LikertCard._saveResponse's handling of the same field.
                    const body = await res.json().catch(() => null);
                    if (body && body.early_result && typeof Results !== 'undefined') {
                        Results.applyEarlyResult(body.early_result);
                    }
                    // Tell the agent the scenario is answered so it advances to
                    // the next Tier-1 item. Reuses LikertCard's batch_complete
                    // signal, which the assessment agent already handles. Not
                    // re-fired when editing an already-answered (history-
                    // replayed) scenario -- notified starts true in that case.
                    if (!notified) {
                        notified = true;
                        _notifyScenarioAnswered(data.scenario_id);
                    }
                } catch (err) {
                    console.error('[ScenarioCard] Save error:', err.message);
                }
            });

            choicesEl.appendChild(btn);
        }

        card.appendChild(choicesEl);
        return card;
    }

    async function _notifyScenarioAnswered(scenarioId) {
        const sessionId = App.getCurrentSessionId();
        if (!sessionId) return;
        try {
            // Reuse LikertCard's batch_complete signal: the assessment agent
            // treats it as "response saved, advance to the next item".
            await Chat.sendMessage(sessionId, JSON.stringify({
                type: 'batch_complete',
                batch_id: scenarioId
            }));
        } catch (err) {
            console.error('[ScenarioCard] Advance notification failed:', err.message);
        }
    }

    return { create };
})();
