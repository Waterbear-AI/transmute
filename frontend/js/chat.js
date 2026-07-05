/* Transmutation Engine - Chat Module */
'use strict';

const Chat = (() => {
    let _abortController = null;
    let _currentMessageEl = null;
    let _isReadOnly = false;
    let _isLoading = false;
    let _pendingWidgets = [];

    const _messagesEl = () => document.getElementById('chat-messages');

    /**
     * Initialize the chat module — bind form submit.
     */
    function init() {
        const form = document.getElementById('chat-form');
        form.addEventListener('submit', _handleSubmit);
    }

    function setReadOnly(readOnly) {
        _isReadOnly = readOnly;
        const input = document.getElementById('chat-input');
        const btn = document.querySelector('.chat-send-btn');
        input.disabled = readOnly;
        btn.disabled = readOnly;
        input.placeholder = readOnly ? 'This session is read-only' : 'Type a message...';
    }

    function clear() {
        _messagesEl().textContent = '';
        _currentMessageEl = null;
    }

    async function _handleSubmit(e) {
        e.preventDefault();
        if (_isReadOnly) return;

        const input = document.getElementById('chat-input');
        const message = input.value.trim();
        if (!message) return;

        const sessionId = App.getCurrentSessionId();
        if (!sessionId) return;

        input.value = '';
        _appendUserMessage(message);
        _setLoading(true);
        try {
            await sendMessage(sessionId, message);
        } finally {
            _setLoading(false);
            input.focus();
        }
    }

    function _setLoading(loading) {
        _isLoading = loading;
        const input = document.getElementById('chat-input');
        const btn = document.querySelector('.chat-send-btn');
        if (loading) {
            input.disabled = true;
            btn.disabled = true;
            btn.classList.add('chat-send-btn--loading');
        } else {
            input.disabled = _isReadOnly;
            btn.disabled = _isReadOnly;
            btn.classList.remove('chat-send-btn--loading');
        }
    }

    /**
     * Trigger the agent's first turn without a user message.
     * Used by Sessions.activate() when a session loads with empty history
     * so the agent greets automatically (signup, new "New" sessions).
     * Mirrors sendMessage but does NOT render a user message, does NOT
     * disable the input, and posts no body to /api/chat/{id}/start.
     */
    async function startSession(sessionId) {
        if (_isReadOnly) return;
        if (_abortController) _abortController.abort();
        _abortController = new AbortController();

        _showThinkingIndicator();

        const timeoutWarning = setTimeout(() => {
            Toast.show('Still waiting for a response... this is taking longer than usual.', 'warning');
        }, 15000);

        try {
            const res = await fetch('/api/chat/' + encodeURIComponent(sessionId) + '/start', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({}),
                signal: _abortController.signal
            });

            if (!res.ok) {
                const err = await res.json().catch(() => ({}));
                const errMsg = err.detail || 'Could not start conversation';
                _appendSystemMessage('Error: ' + errMsg);
                Toast.show(errMsg, 'error', {
                    onRetry: () => startSession(sessionId)
                });
                return;
            }

            await _parseSSEStream(res.body);
        } catch (err) {
            if (err.name !== 'AbortError') {
                _appendSystemMessage('Connection error: ' + err.message);
                Toast.show('Connection error: ' + err.message, 'error', {
                    onRetry: () => startSession(sessionId)
                });
            }
        } finally {
            clearTimeout(timeoutWarning);
            _abortController = null;
        }
    }

    /**
     * Send a message and stream the SSE response.
     */
    async function sendMessage(sessionId, message) {
        if (_abortController) _abortController.abort();
        _abortController = new AbortController();

        // Show the indicator on every send — widget submissions (Likert,
        // StructuredChoice) call this directly and would otherwise leave the
        // user staring at a frozen UI while the agent thinks.
        _showThinkingIndicator();

        const timeoutWarning = setTimeout(() => {
            Toast.show('Still waiting for a response... this is taking longer than usual.', 'warning');
        }, 15000);

        try {
            const res = await fetch('/api/chat/' + encodeURIComponent(sessionId), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message }),
                signal: _abortController.signal
            });

            if (!res.ok) {
                const err = await res.json().catch(() => ({}));
                const errMsg = err.detail || 'Failed to send message';
                _appendSystemMessage('Error: ' + errMsg);
                Toast.show(errMsg, 'error', {
                    onRetry: () => sendMessage(sessionId, message)
                });
                return;
            }

            await _parseSSEStream(res.body);
        } catch (err) {
            if (err.name !== 'AbortError') {
                _appendSystemMessage('Connection error: ' + err.message);
                Toast.show('Connection error: ' + err.message, 'error', {
                    onRetry: () => sendMessage(sessionId, message)
                });
            }
        } finally {
            clearTimeout(timeoutWarning);
            _abortController = null;
        }
    }

    /**
     * Parse SSE events from a ReadableStream (for POST responses).
     */
    async function _parseSSEStream(body) {
        const reader = body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const events = _extractSSEEvents(buffer);
            buffer = events.remaining;

            for (const evt of events.parsed) {
                _handleSSEEvent(evt.event, evt.data);
            }
        }

        // Finalize any remaining agent message and flush buffered widgets.
        // Stream is done — drop the indicator unconditionally; if more is
        // coming it will be a separate stream and re-show itself.
        _currentMessageEl = null;
        _flushPendingWidgets();
        _removeThinkingIndicator();
    }

    /**
     * Extract complete SSE events from a buffer string.
     * Returns {parsed: [{event, data}], remaining: string}
     */
    function _extractSSEEvents(buffer) {
        const parsed = [];
        const blocks = buffer.split('\n\n');
        const remaining = blocks.pop(); // incomplete block stays in buffer

        for (const block of blocks) {
            if (!block.trim()) continue;
            let eventType = 'message';
            let dataLines = [];

            for (const line of block.split('\n')) {
                if (line.startsWith('event: ')) {
                    eventType = line.slice(7).trim();
                } else if (line.startsWith('data: ')) {
                    dataLines.push(line.slice(6));
                } else if (line.startsWith('data:')) {
                    dataLines.push(line.slice(5));
                }
            }

            if (dataLines.length > 0) {
                try {
                    const data = JSON.parse(dataLines.join('\n'));
                    parsed.push({ event: eventType, data });
                } catch (err) {
                    console.warn('[Chat] Failed to parse SSE data:', dataLines.join('\n'));
                }
            }
        }

        return { parsed, remaining };
    }

    /**
     * Route SSE events to appropriate handlers.
     */
    function _handleSSEEvent(eventType, data) {
        switch (eventType) {
            case 'agent.thinking':
                _showThinkingIndicator();
                break;

            case 'agent.message.chunk':
                _appendAgentChunk(data.text);
                break;

            case 'agent.message.complete':
                _finalizeAgentMessage(data.text);
                _flushPendingWidgets();
                break;

            case 'tool.call':
                // Tool calls are internal agent mechanics — don't show to users
                break;

            case 'tool.result':
                // Tool results are internal — don't show to users
                break;

            case 'error':
                _appendSystemMessage('Error: ' + (data.message || 'Unknown error'));
                Toast.show(data.message || 'Unknown error', 'error');
                break;

            case 'session.cost':
                _updateCostDisplay(data);
                break;

            case 'phase.transition':
                _appendPhaseTransition(data.from, data.to);
                if (typeof Results !== 'undefined') {
                    Results.handlePhaseTransition(data.from, data.to);
                }
                Toast.show('Entering ' + data.to.charAt(0).toUpperCase() + data.to.slice(1) + ' phase', 'success');
                break;

            // Domain events — buffer widgets to display after agent message
            case 'assessment.question_batch':
                _pendingWidgets.push(() => LikertCard.create(data));
                break;

            case 'assessment.scenario':
                _pendingWidgets.push(() => ScenarioCard.create(data));
                break;

            case 'education.comprehension':
                // Guard: record_comprehension_answer feedback also carries event_type
                // "education.comprehension" but has no options. Only create the
                // interactive card when options is a non-empty array.
                if (Array.isArray(data.options) && data.options.length) {
                    _pendingWidgets.push(() => StructuredChoice.create(data));
                }
                break;

            case 'education.continue':
                // Interactive "Continue" button — replaces free-text
                // "ready to continue?" prompts. ContinuePrompt defaults
                // label/message, so a sparse payload still renders safely.
                _pendingWidgets.push(() => ContinuePrompt.create(data));
                break;

            case 'education.content':
                // The coach's teaching explanation arrives as one complete
                // block (not streamed chunks) — render it as its own fresh
                // agent message immediately, rather than buffering like the
                // interactive widgets above.
                _appendEducationContent(data.content);
                break;

            case 'assessment.progress':
            case 'assessment.transmute_result':
            case 'profile.snapshot':
            case 'education.progress':
            case 'development.roadmap':
            case 'development.practice':
            case 'graduation.readiness':
            case 'graduation.complete':
            case 'checkin.complete':
                if (typeof Results !== 'undefined') {
                    Results.handleSSEEvent(eventType, data);
                }
                break;

            default:
                console.log('[Chat] Unhandled SSE event:', eventType, data);
        }
    }

    // ── Message rendering ──────────────────────

    /**
     * Render an education.content payload as a fresh agent-styled message:
     * a brand-new bubble (not appended to any in-flight streamed message),
     * containing the captured teaching text rendered as sanitized markdown
     * via the shared Markdown module. Used for both the live SSE event and
     * history replay so chat and the learning journal stay byte-identical.
     */
    function _appendEducationContent(content) {
        _removeThinkingIndicator();
        const el = document.createElement('div');
        el.className = 'chat-msg chat-msg--agent';
        Markdown.render(el, content);
        _messagesEl().appendChild(el);
        // A fresh bubble was just created outside the normal chunk-append
        // flow — reset _currentMessageEl so any subsequent streamed text
        // opens its own new bubble instead of appending here.
        _currentMessageEl = null;
        _scrollToBottom();
        return el;
    }

    function _appendUserMessage(text) {
        const el = document.createElement('div');
        el.className = 'chat-msg chat-msg--user';
        Sanitize.setText(el, text);
        _messagesEl().appendChild(el);
        _scrollToBottom();
    }

    function _showThinkingIndicator() {
        _removeThinkingIndicator();
        const el = document.createElement('div');
        el.className = 'chat-msg chat-msg--thinking';
        el.id = 'thinking-indicator';
        Sanitize.setText(el, 'Agent is thinking...');
        _messagesEl().appendChild(el);
        _scrollToBottom();
    }

    function _removeThinkingIndicator() {
        const existing = document.getElementById('thinking-indicator');
        if (existing) existing.remove();
    }

    function _appendAgentChunk(text) {
        _removeThinkingIndicator();
        if (!_currentMessageEl) {
            _currentMessageEl = document.createElement('div');
            _currentMessageEl.className = 'chat-msg chat-msg--agent';
            _messagesEl().appendChild(_currentMessageEl);
        }
        _currentMessageEl.appendChild(Sanitize.textNode(text));
        _scrollToBottom();
    }

    function _finalizeAgentMessage(fullText) {
        _removeThinkingIndicator();
        if (!_currentMessageEl) {
            _currentMessageEl = document.createElement('div');
            _currentMessageEl.className = 'chat-msg chat-msg--agent';
            _messagesEl().appendChild(_currentMessageEl);
        }
        // Render markdown through the shared XSS-safe Markdown module
        Markdown.render(_currentMessageEl, fullText);
        _currentMessageEl = null;
        _scrollToBottom();

        // The stream is still open — a sub-agent (phase transfer) or a tool
        // round-trip may keep producing. Show the indicator so the user
        // doesn't see a silent gap. _parseSSEStream clears it on stream end.
        if (_abortController) {
            _showThinkingIndicator();
        }
    }

    function _appendToolCall(toolName, args) {
        const container = document.createElement('div');
        container.className = 'chat-tool-call';

        const header = document.createElement('div');
        header.className = 'chat-tool-call__header';

        const chevron = document.createElement('span');
        chevron.className = 'chat-tool-call__chevron';
        chevron.textContent = '\u25B6';
        header.appendChild(chevron);

        const nameEl = document.createElement('span');
        nameEl.className = 'chat-tool-call__name';
        Sanitize.setText(nameEl, toolName);
        header.appendChild(nameEl);

        container.appendChild(header);

        const body = document.createElement('div');
        body.className = 'chat-tool-call__body';
        Sanitize.setText(body, JSON.stringify(args, null, 2));
        container.appendChild(body);

        header.addEventListener('click', () => {
            container.classList.toggle('chat-tool-call--expanded');
        });

        _messagesEl().appendChild(container);
        _scrollToBottom();
    }

    function _appendToolResult(toolName, response) {
        // Tool results update the last tool call's body if present
        const toolCalls = _messagesEl().querySelectorAll('.chat-tool-call');
        if (toolCalls.length > 0) {
            const last = toolCalls[toolCalls.length - 1];
            const body = last.querySelector('.chat-tool-call__body');
            if (body) {
                const resultText = typeof response === 'object'
                    ? JSON.stringify(response, null, 2)
                    : String(response);
                body.appendChild(document.createTextNode('\n\nResult: ' + resultText));
            }
        }
    }

    function _appendSystemMessage(text) {
        _removeThinkingIndicator();
        const el = document.createElement('div');
        el.className = 'chat-msg chat-msg--system';
        Sanitize.setText(el, text);
        _messagesEl().appendChild(el);
        _scrollToBottom();
    }

    function _appendPhaseTransition(from, to) {
        const fromLabel = from.charAt(0).toUpperCase() + from.slice(1);
        const toLabel = to.charAt(0).toUpperCase() + to.slice(1);
        _appendSystemMessage('Phase transition: ' + fromLabel + ' \u2192 ' + toLabel);
        // Sub-agent for the new phase is still generating \u2014 _appendSystemMessage
        // stripped the indicator, so put it back while the stream is alive.
        if (_abortController) {
            _showThinkingIndicator();
        }
    }

    function _flushPendingWidgets() {
        for (const createFn of _pendingWidgets) {
            _appendWidget(createFn);
        }
        _pendingWidgets = [];
    }

    function _appendWidget(createFn) {
        try {
            const el = createFn();
            if (el) {
                _messagesEl().appendChild(el);
                _scrollToBottom();
            }
        } catch (err) {
            console.error('[Chat] Widget creation failed:', err);
        }
    }

    // Last known lifetime total across all the user's sessions. Seeded on load
    // from /api/sessions and refreshed live from the session.cost SSE payload.
    let _lastUserTotal = 0;

    function _renderCost(sessionCost, totalCost) {
        const el = document.getElementById('cost-display');
        if (!el) return;
        const s = (typeof sessionCost === 'number' ? sessionCost : 0).toFixed(2);
        const t = (typeof totalCost === 'number' ? totalCost : 0).toFixed(2);
        Sanitize.setText(el, 'Est. cost: $' + s + ' (total $' + t + ')');
    }

    function _updateCostDisplay(data) {
        // Prefer session-cumulative total; fall back to per-turn for old payloads.
        const sessionCost = typeof data.session_cost_usd === 'number'
            ? data.session_cost_usd
            : (data.estimated_cost_usd || 0);
        if (typeof data.user_total_cost_usd === 'number') {
            _lastUserTotal = data.user_total_cost_usd;
        }
        _renderCost(sessionCost, _lastUserTotal);
    }

    // Seed the lifetime total on load (before any chat turn this session), so the
    // top bar shows "Est. cost: $0.00 (total $X)" immediately.
    function seedCostTotal(total) {
        if (typeof total === 'number') _lastUserTotal = total;
        _renderCost(0, _lastUserTotal);
    }

    function _scrollToBottom() {
        const container = _messagesEl();
        container.scrollTop = container.scrollHeight;
    }

    /**
     * Load conversation history for a session (for viewing past sessions).
     */
    // Control messages are machine payloads the widgets send TO the agent
    // (not human-typed). They are JSON-encoded with one of these `type`
    // discriminators. Live, sendMessage never renders a user bubble for them;
    // on reload they come back as role:'user' history rows and must NOT be
    // shown as raw JSON. Keep this list in sync with the widgets that call
    // Chat.sendMessage(JSON.stringify({type: ...})).
    const _CONTROL_MESSAGE_TYPES = new Set([
        'comprehension_answer',  // StructuredChoice
        'batch_complete',        // LikertCard
        'continue',              // ContinuePrompt
    ]);

    // Returns true when `text` is a JSON control payload that should be hidden
    // from the chat transcript. Guarded so ordinary text (which is not valid
    // JSON, or is JSON without a recognized control type) still renders.
    function _isControlMessage(text) {
        if (typeof text !== 'string') return false;
        const trimmed = text.trim();
        if (!trimmed.startsWith('{')) return false;  // fast path: not an object
        try {
            const parsed = JSON.parse(trimmed);
            return !!parsed
                && typeof parsed === 'object'
                && _CONTROL_MESSAGE_TYPES.has(parsed.type);
        } catch (e) {
            return false;  // not JSON → a normal user message, render it
        }
    }

    function renderHistory(messages, answeredResponses, scenarioResponses) {
        clear();
        const answers = answeredResponses || {};
        const scenarioAnswers = scenarioResponses || {};
        for (const msg of messages) {
            if (msg.role === 'user') {
                // Suppress machine control payloads (e.g. comprehension answers)
                // so they don't appear as raw JSON bubbles after a reload.
                if (_isControlMessage(msg.text)) continue;
                _appendUserMessage(msg.text);
            } else if (msg.role === 'agent') {
                const el = document.createElement('div');
                el.className = 'chat-msg chat-msg--agent';
                Markdown.render(el, msg.text);
                _messagesEl().appendChild(el);
            } else if (msg.role === 'widget') {
                _renderHistoryWidget(msg.event_type, msg.data, answers, scenarioAnswers);
            } else if (msg.role === 'system') {
                _appendSystemMessage(msg.text);
            }
        }
        _scrollToBottom();
    }

    function _renderHistoryWidget(eventType, data, answers, scenarioAnswers) {
        try {
            let el = null;
            switch (eventType) {
                case 'assessment.question_batch':
                    el = LikertCard.create(data, answers);
                    break;
                case 'assessment.scenario':
                    el = ScenarioCard.create(data, (scenarioAnswers || {})[data.scenario_id]?.choice);
                    break;
                case 'education.comprehension':
                    // Guard: feedback events share this event_type but have no options.
                    // Only render the interactive card when options is a non-empty array.
                    if (Array.isArray(data.options) && data.options.length) {
                        el = StructuredChoice.create(data);
                    }
                    break;
                case 'education.continue':
                    el = ContinuePrompt.create(data);
                    break;
                case 'education.content':
                    // Replays as a fresh agent markdown message, identical to
                    // the live-render path — the function_response event's
                    // stored `content` is the same text the user originally saw.
                    _appendEducationContent(data.content);
                    return;
                case 'phase.transition':
                    _appendPhaseTransition(data.from, data.to);
                    return;
            }
            if (el) {
                _messagesEl().appendChild(el);
                _scrollToBottom();
            }
        } catch (err) {
            console.error('[Chat] History widget render failed:', eventType, err);
        }
    }

    return {
        init,
        setReadOnly,
        clear,
        sendMessage,
        startSession,
        renderHistory,
        seedCostTotal,
        appendSystemMessage: _appendSystemMessage
    };
})();
