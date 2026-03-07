/* Transmutation Engine - StructuredChoice Component */
'use strict';

const StructuredChoice = (() => {

    /**
     * Create a StructuredChoice from an education.comprehension SSE event.
     * data: {dimension, category, question_id, stem, options: [{key, text}]}
     */
    function create(data) {
        const card = document.createElement('div');
        card.className = 'widget-card';
        card.setAttribute('role', 'region');
        card.setAttribute('aria-label', 'Comprehension check');

        const stem = document.createElement('div');
        stem.className = 'structured-stem';
        Sanitize.setText(stem, data.stem || data.question || '');
        card.appendChild(stem);

        const optionsEl = document.createElement('div');
        optionsEl.className = 'structured-options';

        const options = data.options || [];
        for (const opt of options) {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'structured-option';
            Sanitize.setText(btn, (opt.key ? opt.key + '. ' : '') + opt.text);

            // Keyboard navigation: arrow keys between options
            btn.addEventListener('keydown', (e) => {
                const btns = Array.from(optionsEl.querySelectorAll('.structured-option:not(:disabled)'));
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

            btn.addEventListener('click', () => {
                if (btn.disabled) return;

                btn.classList.add('structured-option--selected');
                optionsEl.querySelectorAll('.structured-option').forEach(b => {
                    b.disabled = true;
                });

                // Response handled by agent via record_comprehension_answer tool call.
                // No direct API POST needed — the agent observes the selection
                // through the chat stream.
                const sessionId = App.getCurrentSessionId();
                if (sessionId) {
                    Chat.sendMessage(sessionId, JSON.stringify({
                        type: 'comprehension_answer',
                        question_id: data.question_id,
                        selected_key: opt.key
                    }));
                }
            });

            optionsEl.appendChild(btn);
        }

        card.appendChild(optionsEl);
        return card;
    }

    return { create };
})();
