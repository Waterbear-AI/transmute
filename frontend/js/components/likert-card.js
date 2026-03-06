/* Transmutation Engine - LikertBatchCard Component */
'use strict';

const LikertCard = (() => {

    /**
     * Create a LikertBatchCard from an assessment.question_batch SSE event.
     * data: {batch_id, sub_dimension, dimension, questions: [{id, text, scale_type, scale_labels}]}
     */
    function create(data) {
        const card = document.createElement('div');
        card.className = 'widget-card';
        card.setAttribute('role', 'region');
        card.setAttribute('aria-label', 'Assessment questions: ' + (data.sub_dimension || data.dimension));

        const title = document.createElement('div');
        title.className = 'widget-card__title';
        Sanitize.setText(title, data.sub_dimension || data.dimension);
        card.appendChild(title);

        const answeredSet = new Set();
        const totalQuestions = data.questions.length;

        for (const q of data.questions) {
            const questionEl = _createQuestion(q, data.batch_id, answeredSet, totalQuestions);
            card.appendChild(questionEl);
        }

        return card;
    }

    function _createQuestion(question, batchId, answeredSet, totalQuestions) {
        const container = document.createElement('div');
        container.className = 'likert-question';

        const textEl = document.createElement('div');
        textEl.className = 'likert-question__text';
        Sanitize.setText(textEl, question.text);
        container.appendChild(textEl);

        const checkEl = document.createElement('span');
        checkEl.className = 'likert-question__check';
        checkEl.hidden = true;
        checkEl.textContent = '\u2713';
        textEl.appendChild(checkEl);

        const scale = document.createElement('div');
        scale.className = 'likert-scale';
        scale.setAttribute('role', 'radiogroup');
        scale.setAttribute('aria-label', question.text);

        const labels = question.scale_labels || ['SD', 'D', 'N', 'A', 'SA'];

        labels.forEach((label, index) => {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'likert-option';
            btn.setAttribute('role', 'radio');
            btn.setAttribute('aria-checked', 'false');
            btn.setAttribute('aria-label', label);
            btn.setAttribute('tabindex', index === 0 ? '0' : '-1');
            Sanitize.setText(btn, label);

            btn.addEventListener('click', async () => {
                if (btn.disabled) return;

                // Visual selection
                scale.querySelectorAll('.likert-option').forEach(b => {
                    b.classList.remove('likert-option--selected');
                    b.setAttribute('aria-checked', 'false');
                });
                btn.classList.add('likert-option--selected');
                btn.setAttribute('aria-checked', 'true');

                // POST to API
                const score = index + 1;
                const ok = await _saveResponse(question.id, 'likert', score);
                if (ok) {
                    checkEl.hidden = false;
                    answeredSet.add(question.id);

                    // Disable all options for this question
                    scale.querySelectorAll('.likert-option').forEach(b => {
                        b.disabled = true;
                    });

                    // Check batch completion
                    if (answeredSet.size === totalQuestions) {
                        _notifyBatchComplete(batchId);
                    }
                }
            });

            // Keyboard navigation: arrow keys within radio group
            btn.addEventListener('keydown', (e) => {
                const options = Array.from(scale.querySelectorAll('.likert-option:not(:disabled)'));
                const idx = options.indexOf(btn);
                let target = null;

                if (e.key === 'ArrowRight' || e.key === 'ArrowDown') {
                    target = options[(idx + 1) % options.length];
                } else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
                    target = options[(idx - 1 + options.length) % options.length];
                }

                if (target) {
                    e.preventDefault();
                    options.forEach(o => o.setAttribute('tabindex', '-1'));
                    target.setAttribute('tabindex', '0');
                    target.focus();
                }
            });

            scale.appendChild(btn);
        });

        container.appendChild(scale);
        return container;
    }

    async function _saveResponse(questionId, type, score) {
        try {
            const res = await fetch('/api/assessment/responses', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    question_id: questionId,
                    type: type,
                    score: score
                })
            });
            if (!res.ok) {
                console.error('[LikertCard] Save failed:', res.status);
                return false;
            }
            return true;
        } catch (err) {
            console.error('[LikertCard] Save error:', err.message);
            return false;
        }
    }

    async function _notifyBatchComplete(batchId) {
        const sessionId = App.getCurrentSessionId();
        if (!sessionId) return;

        try {
            await Chat.sendMessage(sessionId, JSON.stringify({
                type: 'batch_complete',
                batch_id: batchId
            }));
        } catch (err) {
            console.error('[LikertCard] Batch notification failed:', err.message);
        }
    }

    return { create };
})();
