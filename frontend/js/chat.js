/* Transmutation Engine - Chat Module */
'use strict';

const Chat = (() => {
    let _abortController = null;
    let _currentMessageEl = null;
    let _isReadOnly = false;
    let _isLoading = false;

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
     * Send a message and stream the SSE response.
     */
    async function sendMessage(sessionId, message) {
        if (_abortController) _abortController.abort();
        _abortController = new AbortController();

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

        // Finalize any remaining agent message
        _currentMessageEl = null;
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
                break;

            case 'tool.call':
                _appendToolCall(data.name, data.args);
                break;

            case 'tool.result':
                _appendToolResult(data.name, data.response);
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

            // Domain events — dispatch to widget creators or Results panel
            case 'assessment.question_batch':
                _appendWidget(() => LikertCard.create(data));
                break;

            case 'assessment.scenario':
                _appendWidget(() => ScenarioCard.create(data));
                break;

            case 'education.comprehension':
                _appendWidget(() => StructuredChoice.create(data));
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
            // No chunks were streamed — create the message element now
            _currentMessageEl = document.createElement('div');
            _currentMessageEl.className = 'chat-msg chat-msg--agent';
            _messagesEl().appendChild(_currentMessageEl);
        }
        Sanitize.setText(_currentMessageEl, fullText);
        _currentMessageEl = null;
        _scrollToBottom();
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

    function _updateCostDisplay(data) {
        const el = document.getElementById('cost-display');
        if (el) {
            const cost = (data.estimated_cost_usd || 0).toFixed(2);
            Sanitize.setText(el, 'Est. cost: $' + cost);
        }
    }

    function _scrollToBottom() {
        const container = _messagesEl();
        container.scrollTop = container.scrollHeight;
    }

    /**
     * Load conversation history for a session (for viewing past sessions).
     */
    function renderHistory(messages) {
        clear();
        for (const msg of messages) {
            if (msg.role === 'user') {
                _appendUserMessage(msg.text);
            } else if (msg.role === 'agent') {
                const el = document.createElement('div');
                el.className = 'chat-msg chat-msg--agent';
                Sanitize.setText(el, msg.text);
                _messagesEl().appendChild(el);
            } else if (msg.role === 'system') {
                _appendSystemMessage(msg.text);
            }
        }
    }

    return {
        init,
        setReadOnly,
        clear,
        sendMessage,
        renderHistory,
        appendSystemMessage: _appendSystemMessage
    };
})();
