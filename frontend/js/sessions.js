/* Transmutation Engine - Session Management Module */
'use strict';

const Sessions = (() => {
    let _sessions = [];
    let _activeSessionId = null;

    /**
     * Render session list in the bottom bar.
     */
    function render(sessions) {
        _sessions = sessions || [];
        const listEl = document.getElementById('session-list');
        listEl.textContent = '';

        // New session button
        const newBtn = document.createElement('button');
        newBtn.className = 'session-btn session-btn--new';
        newBtn.type = 'button';
        newBtn.textContent = 'New';
        newBtn.addEventListener('click', createNew);
        listEl.appendChild(newBtn);

        // Existing sessions
        for (const session of _sessions) {
            const btn = document.createElement('button');
            btn.className = 'session-btn';
            btn.type = 'button';
            if (session.session_id === _activeSessionId) {
                btn.classList.add('session-btn--active');
            }

            const label = _formatSessionLabel(session);
            Sanitize.setText(btn, label);

            btn.addEventListener('click', () => activate(session.session_id));
            listEl.appendChild(btn);
        }
    }

    /**
     * Activate a session — load its conversation.
     */
    function activate(sessionId) {
        _activeSessionId = sessionId;
        App.setCurrentSessionId(sessionId);

        const session = _sessions.find(s => s.session_id === sessionId);
        const isArchived = session && session.archived;

        Chat.clear();
        Chat.setReadOnly(!!isArchived);

        // Re-render to update active styling
        render(_sessions);

        // Load conversation history if available
        if (session && session.messages) {
            Chat.renderHistory(session.messages);
        }
    }

    /**
     * Create a new session.
     */
    async function createNew() {
        try {
            const res = await fetch('/api/sessions', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({})
            });

            if (!res.ok) {
                console.error('[Sessions] Create failed:', res.status);
                return;
            }

            const newSession = await res.json();
            _sessions.unshift(newSession);
            activate(newSession.session_id);
            render(_sessions);
        } catch (err) {
            console.error('[Sessions] Create error:', err.message);
        }
    }

    function _formatSessionLabel(session) {
        const date = new Date(session.created_at);
        const month = date.toLocaleString('default', { month: 'short' });
        const day = date.getDate();
        const phase = session.current_phase || '';
        const phaseLabel = phase ? ' - ' + phase.charAt(0).toUpperCase() + phase.slice(1) : '';
        return month + ' ' + day + phaseLabel;
    }

    return {
        render,
        activate,
        createNew
    };
})();
