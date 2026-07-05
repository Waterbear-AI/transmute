/* Transmutation Engine - ScenarioCard Component */
'use strict';

const ScenarioCard = (() => {

    /**
     * Create a ScenarioCard from an assessment.scenario SSE event.
     * data: {scenario_id, dimension, narrative, choices: [{key, text}], has_follow_up}
     */
    function create(data) {
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

        // Fire the agent-advance signal at most once per scenario, even if the
        // user re-selects to correct a mis-click.
        let notified = false;

        for (const choice of data.choices) {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'scenario-choice';

            const keySpan = document.createElement('span');
            keySpan.className = 'scenario-choice__key';
            Sanitize.setText(keySpan, choice.key);
            btn.appendChild(keySpan);

            btn.appendChild(Sanitize.textNode(choice.text));

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
                // the user can correct a mis-click). The API upserts the response.
                choicesEl.querySelectorAll('.scenario-choice').forEach(b => {
                    b.classList.remove('scenario-choice--selected');
                });
                btn.classList.add('scenario-choice--selected');

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
                    // Tell the agent the scenario is answered so it advances to
                    // the next Tier-1 item. Reuses LikertCard's batch_complete
                    // signal, which the assessment agent already handles.
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
