/* Transmutation Engine - LikertBatchCard Component */
'use strict';

const LikertCard = (() => {

    /**
     * Create a LikertBatchCard from an assessment.question_batch SSE event.
     * data: {batch_id, sub_dimension, dimension, questions: [{id, text, scale_type, scale_labels}]}
     * answeredResponses: optional {question_id: {score, ...}} for pre-filling history
     */
    function create(data, answeredResponses) {
        const answers = answeredResponses || {};
        const card = document.createElement('div');
        card.className = 'widget-card';
        card.setAttribute('role', 'region');
        card.setAttribute('aria-label', 'Assessment questions: ' + (data.sub_dimension || data.dimension));

        const answeredSet = new Set();
        const totalQuestions = data.questions.length;

        // Pre-count already answered questions
        for (const q of data.questions) {
            if (answers[q.id]) answeredSet.add(q.id);
        }

        // Batch progress header
        const progressHeader = document.createElement('div');
        progressHeader.className = 'likert-batch-progress';

        const titleEl = document.createElement('span');
        titleEl.className = 'likert-batch-progress__title';
        Sanitize.setText(titleEl, data.sub_dimension || data.dimension || 'Questions');
        progressHeader.appendChild(titleEl);

        const counterEl = document.createElement('span');
        counterEl.className = 'likert-batch-progress__counter';
        progressHeader.appendChild(counterEl);

        card.appendChild(progressHeader);

        // Mini progress bar
        const miniBar = document.createElement('div');
        miniBar.className = 'likert-batch-bar';
        const miniBarFill = document.createElement('div');
        miniBarFill.className = 'likert-batch-bar__fill';
        miniBar.appendChild(miniBarFill);
        card.appendChild(miniBar);

        const updateProgress = () => {
            const count = answeredSet.size;
            Sanitize.setText(counterEl, count + ' / ' + totalQuestions + ' answered');
            miniBarFill.style.width = (count / totalQuestions * 100) + '%';
            if (count === totalQuestions) {
                progressHeader.classList.add('likert-batch-progress--complete');
                Sanitize.setText(counterEl, 'All done!');
            }
        };

        // Set initial progress
        updateProgress();

        for (const q of data.questions) {
            const prefilled = answers[q.id] || null;
            const questionEl = _createQuestion(q, data.batch_id, answeredSet, totalQuestions, updateProgress, prefilled);
            card.appendChild(questionEl);
        }

        return card;
    }

    function _createQuestion(question, batchId, answeredSet, totalQuestions, onAnswer, prefilled) {
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

        const labels = question.scale_labels || ['Strongly Disagree', 'Disagree', 'Neutral', 'Agree', 'Strongly Agree'];

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

                    // Update batch progress
                    onAnswer();

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

        // Pre-fill if already answered (history mode)
        if (prefilled && prefilled.score != null) {
            const selectedIdx = prefilled.score - 1; // score is 1-based
            const buttons = scale.querySelectorAll('.likert-option');
            if (selectedIdx >= 0 && selectedIdx < buttons.length) {
                buttons[selectedIdx].classList.add('likert-option--selected');
                buttons[selectedIdx].setAttribute('aria-checked', 'true');
                buttons.forEach(b => { b.disabled = true; });
                checkEl.hidden = false;
            }
        }

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
