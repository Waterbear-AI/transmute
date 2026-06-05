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
                _pendingWidgets.push(() => StructuredChoice.create(data));
                break;

            case 'assessment.progress':
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

    // ── Markdown conversion ──────────────────────

    /**
     * Convert basic markdown to HTML. The output is then run through
     * Sanitize.sanitizeHTML() so only allowlisted tags survive.
     */
    function _markdownToHTML(text) {
        let html = text;
        // Code blocks (``` ... ```)
        html = html.replace(/```(\w*)\n?([\s\S]*?)```/g, '<pre><code>$2</code></pre>');
        // Inline code
        html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
        // Headers (#, ##, ###, ####). Longest fences first so e.g. "### x"
        // is not matched by the "## " rule (## requires a space after, which
        // "###" lacks) — order is belt-and-suspenders.
        html = html.replace(/^#### (.+)$/gm, '<h4>$1</h4>');
        html = html.replace(/^### (.+)$/gm, '<h3>$1</h3>');
        html = html.replace(/^## (.+)$/gm, '<h2>$1</h2>');
        html = html.replace(/^# (.+)$/gm, '<h1>$1</h1>');
        // Horizontal rules
        html = html.replace(/^---+$/gm, '<br>');
        // Blockquotes: collapse consecutive lines starting with ">" into a
        // single <blockquote>. Inner content (bold, lists) is handled by the
        // passes below, so this must run before bold/list processing.
        html = html.replace(/(?:^|\n)((?:>[^\n]*(?:\n|$))+)/g, (_, block) => {
            const inner = block.replace(/\n+$/, '').split('\n')
                .map(line => line.replace(/^>\s?/, ''))
                .join('\n');
            // Keep the closing tag on its own line so the list/paragraph passes
            // below don't swallow "</blockquote>" into the final <li>.
            return '\n<blockquote>\n' + inner + '\n</blockquote>\n';
        });
        // Bold + italic
        html = html.replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>');
        // Bold
        html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
        // Italic
        html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');
        // Unordered lists (consecutive lines starting with - )
        html = html.replace(/(?:^|\n)((?:- .+\n?)+)/g, (_, block) => {
            const items = block.trim().split('\n').map(line =>
                '<li>' + line.replace(/^- /, '') + '</li>'
            ).join('');
            return '<ul>' + items + '</ul>';
        });
        // Ordered lists (consecutive lines starting with N. )
        html = html.replace(/(?:^|\n)((?:\d+\. .+\n?)+)/g, (_, block) => {
            const items = block.trim().split('\n').map(line =>
                '<li>' + line.replace(/^\d+\. /, '') + '</li>'
            ).join('');
            return '<ol>' + items + '</ol>';
        });
        // Paragraphs: double newlines become <p> breaks
        html = html.replace(/\n{2,}/g, '</p><p>');
        // Single newlines become <br>
        html = html.replace(/\n/g, '<br>');
        html = '<p>' + html + '</p>';
        // Clean up empty paragraphs
        html = html.replace(/<p>\s*<\/p>/g, '');
        return html;
    }

    // ── Message rendering ──────────────────────

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
        // Render markdown through the XSS-safe sanitizer
        const html = _markdownToHTML(fullText);
        _currentMessageEl.textContent = '';
        _currentMessageEl.appendChild(Sanitize.sanitizeHTML(html));
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
    function renderHistory(messages, answeredResponses) {
        clear();
        const answers = answeredResponses || {};
        for (const msg of messages) {
            if (msg.role === 'user') {
                _appendUserMessage(msg.text);
            } else if (msg.role === 'agent') {
                const el = document.createElement('div');
                el.className = 'chat-msg chat-msg--agent';
                const html = _markdownToHTML(msg.text);
                el.appendChild(Sanitize.sanitizeHTML(html));
                _messagesEl().appendChild(el);
            } else if (msg.role === 'widget') {
                _renderHistoryWidget(msg.event_type, msg.data, answers);
            } else if (msg.role === 'system') {
                _appendSystemMessage(msg.text);
            }
        }
        _scrollToBottom();
    }

    function _renderHistoryWidget(eventType, data, answers) {
        try {
            let el = null;
            switch (eventType) {
                case 'assessment.question_batch':
                    el = LikertCard.create(data, answers);
                    break;
                case 'assessment.scenario':
                    el = ScenarioCard.create(data);
                    break;
                case 'education.comprehension':
                    el = StructuredChoice.create(data);
                    break;
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
