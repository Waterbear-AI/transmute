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

        // Start Over button (full reset)
        const resetBtn = document.createElement('button');
        resetBtn.className = 'session-btn session-btn--reset';
        resetBtn.type = 'button';
        resetBtn.textContent = 'Start Over';
        resetBtn.addEventListener('click', resetAll);
        listEl.appendChild(resetBtn);

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
     * Activate a session — load its conversation history.
     */
    async function activate(sessionId) {
        _activeSessionId = sessionId;
        App.setCurrentSessionId(sessionId);

        const session = _sessions.find(s => s.session_id === sessionId);
        const isArchived = session && session.archived;

        Chat.clear();
        Chat.setReadOnly(!!isArchived);

        // Re-render to update active styling
        render(_sessions);

        // Fetch and display conversation history
        try {
            const res = await fetch('/api/sessions/' + encodeURIComponent(sessionId) + '/history');
            if (res.ok) {
                const data = await res.json();
                if (data.messages && data.messages.length > 0) {
                    Chat.renderHistory(data.messages, data.answered_responses || {});
                }
            }
        } catch (err) {
            console.error('[Sessions] Failed to load history:', err.message);
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

            // Re-fetch full session list (old sessions are now archived server-side)
            const listRes = await fetch('/api/sessions');
            if (listRes.ok) {
                const body = await listRes.json();
                _sessions = body.sessions || body;
            }

            activate(newSession.session_id);
            render(_sessions);
        } catch (err) {
            console.error('[Sessions] Create error:', err.message);
        }
    }

    async function resetAll() {
        if (!confirm('This will erase all your progress and start from the beginning. Are you sure?')) {
            return;
        }

        try {
            const res = await fetch('/api/sessions/reset', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
            });

            if (!res.ok) {
                Toast.show('Failed to reset', 'error');
                return;
            }

            // Full page reload to reset all state (phase stepper, results panel, etc.)
            window.location.reload();
        } catch (err) {
            console.error('[Sessions] Reset error:', err.message);
            Toast.show('Reset failed: ' + err.message, 'error');
        }
    }

    function _formatSessionLabel(session) {
        const msgs = session.message_count || 0;
        if (msgs === 0) return 'Current';

        if (!session.created_at) return 'Session';

        const date = new Date(session.created_at);
        if (isNaN(date.getTime())) return 'Session';

        const now = new Date();
        const diffMs = now - date;
        const diffMins = Math.floor(diffMs / 60000);
        const diffHours = Math.floor(diffMs / 3600000);
        const diffDays = Math.floor(diffMs / 86400000);

        let timeLabel;
        if (diffMins < 1) timeLabel = 'Just now';
        else if (diffMins < 60) timeLabel = diffMins + 'm ago';
        else if (diffHours < 24) timeLabel = diffHours + 'h ago';
        else if (diffDays < 7) timeLabel = diffDays + 'd ago';
        else {
            const month = date.toLocaleString('default', { month: 'short' });
            timeLabel = month + ' ' + date.getDate();
        }

        return timeLabel + ' (' + msgs + ' msgs)';
    }

    return {
        render,
        activate,
        createNew,
        resetAll
    };
})();
