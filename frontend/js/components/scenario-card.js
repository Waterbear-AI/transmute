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

        for (const choice of data.choices) {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'scenario-choice';

            const keySpan = document.createElement('span');
            keySpan.className = 'scenario-choice__key';
            Sanitize.setText(keySpan, choice.key);
            btn.appendChild(keySpan);

            btn.appendChild(Sanitize.textNode(choice.text));

            btn.addEventListener('click', async () => {
                if (btn.disabled) return;

                // Select this choice
                btn.classList.add('scenario-choice--selected');

                // Disable all choices
                choicesEl.querySelectorAll('.scenario-choice').forEach(b => {
                    b.disabled = true;
                });

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

    return { create };
})();
