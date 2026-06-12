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
        card.className = 'widget-card likert-batch';
        card.setAttribute('role', 'region');
        card.setAttribute('aria-label', 'Assessment questions: ' + (data.sub_dimension || data.dimension));

        const answeredSet = new Set();
        const totalQuestions = data.questions.length;

        // Pre-count already answered questions
        for (const q of data.questions) {
            if (answers[q.id]) answeredSet.add(q.id);
        }

        // Batch progress header. Rendered as a <button> so it can act as the
        // expand/collapse toggle once the batch is complete: keyboard-focusable
        // (Enter/Space activates), accessible via aria-expanded.
        const progressHeader = document.createElement('button');
        progressHeader.type = 'button';
        progressHeader.className = 'likert-batch-progress';
        progressHeader.setAttribute('aria-expanded', 'true');

        const chevron = document.createElement('span');
        chevron.className = 'likert-batch-progress__chevron';
        chevron.setAttribute('aria-hidden', 'true');
        chevron.textContent = '▾';  // ▾ (DOWN POINTING SMALL TRIANGLE)
        progressHeader.appendChild(chevron);

        const titleEl = document.createElement('span');
        titleEl.className = 'likert-batch-progress__title';
        Sanitize.setText(titleEl, data.sub_dimension || data.dimension || 'Questions');
        progressHeader.appendChild(titleEl);

        const counterEl = document.createElement('span');
        counterEl.className = 'likert-batch-progress__counter';
        progressHeader.appendChild(counterEl);

        card.appendChild(progressHeader);

        // Mini progress bar (lives outside the body wrapper so it stays
        // visible even when the batch is collapsed).
        const miniBar = document.createElement('div');
        miniBar.className = 'likert-batch-bar';
        const miniBarFill = document.createElement('div');
        miniBarFill.className = 'likert-batch-bar__fill';
        miniBar.appendChild(miniBarFill);
        card.appendChild(miniBar);

        // Body wrapper holds the questions; hidden via CSS when collapsed.
        const body = document.createElement('div');
        body.className = 'likert-batch__body';
        card.appendChild(body);

        const setCollapsed = (collapsed) => {
            card.classList.toggle('likert-batch--collapsed', collapsed);
            progressHeader.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
            chevron.textContent = collapsed ? '▸' : '▾';  // ▸ vs ▾
        };

        const isComplete = () => answeredSet.size === totalQuestions;

        // Fire batch_complete to the agent at most once. Initialised to true when
        // the batch renders already complete (history / prefilled), so editing a
        // past answer re-saves (upsert) without re-signalling completion.
        let batchNotified = isComplete();
        const notifyBatchCompleteOnce = () => {
            if (batchNotified) return;
            batchNotified = true;
            _notifyBatchComplete(data.batch_id);
        };

        const updateProgress = () => {
            const count = answeredSet.size;
            Sanitize.setText(counterEl, count + ' / ' + totalQuestions + ' answered');
            miniBarFill.style.width = (count / totalQuestions * 100) + '%';
            if (count === totalQuestions) {
                progressHeader.classList.add('likert-batch-progress--complete');
                Sanitize.setText(counterEl, 'All done!');
            }
        };

        // Header toggles only after the batch is complete. Clicking it
        // mid-batch would hide the questions the user still needs to answer.
        progressHeader.addEventListener('click', () => {
            if (!isComplete()) return;
            const collapsed = card.classList.contains('likert-batch--collapsed');
            setCollapsed(!collapsed);
        });

        // Set initial progress
        updateProgress();

        for (const q of data.questions) {
            const prefilled = answers[q.id] || null;
            const questionEl = _createQuestion(q, notifyBatchCompleteOnce, answeredSet, totalQuestions, () => {
                updateProgress();
                // Auto-collapse the whole batch the moment the last answer lands.
                if (isComplete()) {
                    setCollapsed(true);
                }
            }, prefilled);
            body.appendChild(questionEl);
        }

        // History mode: if every question was already answered when this card
        // rendered, start collapsed so reloaded batches don't dominate the chat.
        if (isComplete()) {
            setCollapsed(true);
        }

        return card;
    }

    function _createQuestion(question, onBatchComplete, answeredSet, totalQuestions, onAnswer, prefilled) {
        const container = document.createElement('div');
        container.className = 'likert-question';

        const textEl = document.createElement('div');
        textEl.className = 'likert-question__text';
        Sanitize.setText(textEl, question.text);
        container.appendChild(textEl);

        const checkEl = document.createElement('span');
        checkEl.className = 'likert-question__check';
        checkEl.hidden = true;
        checkEl.textContent = '✓';
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

                    // Options stay enabled so the user can correct a mis-click —
                    // re-clicking another option re-saves (the API upserts).

                    // Update batch progress (also handles auto-collapse when done)
                    onAnswer();

                    // Check batch completion (signals the agent at most once)
                    if (answeredSet.size === totalQuestions) {
                        onBatchComplete();
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
            // Forward the returned progress to the Results panel. This save
            // bypasses the agent, so no assessment.progress SSE event will
            // fire — Results would otherwise stay at 0 / 200 until the agent
            // happened to call a tool that emits one.
            const body = await res.json().catch(() => null);
            if (body && body.progress && typeof Results !== 'undefined') {
                Results.handleSSEEvent('assessment.progress', { progress: body.progress });
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
