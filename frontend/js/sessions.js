/* Transmutation Engine - Session Management Module */
'use strict';

const Sessions = (() => {
    let _sessions = [];
    let _activeSessionId = null;
    // session_id being renamed, or null when no rename is in progress
    let _editingSessionId = null;

    // ── Public API ──────────────────────────────────────────────────────────

    /**
     * Render the tab strip (#session-tabs) and the bottom-bar controls
     * (#session-list) from the provided sessions array.
     */
    function render(sessions) {
        _sessions = sessions || [];
        _ensureTabContainer();
        _renderTabs(_sessions);
        _renderBottomBar();
    }

    /**
     * Create #session-tabs inside #chat-panel if the HTML doesn't include it.
     * Supports environments where the static HTML predates this feature.
     */
    function _ensureTabContainer() {
        if (document.getElementById('session-tabs')) return;
        const chatPanel = document.getElementById('chat-panel');
        if (!chatPanel) return;
        const div = document.createElement('div');
        div.id = 'session-tabs';
        div.setAttribute('role', 'tablist');
        div.setAttribute('aria-label', 'Chat sessions');
        chatPanel.appendChild(div);
    }

    /**
     * Activate a session — update state, load history, refresh UI.
     */
    async function activate(sessionId) {
        _activeSessionId = sessionId;
        App.setCurrentSessionId(sessionId);

        const session = _sessions.find(s => s.session_id === sessionId);
        const isArchived = session && session.archived;

        Chat.clear();
        Chat.setReadOnly(!!isArchived);

        // Re-render to update active styling (tab strip + bottom bar).
        render(_sessions);

        try {
            const res = await fetch('/api/sessions/' + encodeURIComponent(sessionId) + '/history');
            if (res.ok) {
                const data = await res.json();
                if (data.messages && data.messages.length > 0) {
                    Chat.renderHistory(data.messages, data.answered_responses || {});
                } else if (!isArchived) {
                    // Fresh session — trigger greeting without user needing to type.
                    Chat.startSession(sessionId);
                }
            }
        } catch (err) {
            console.error('[Sessions] Failed to load history:', err.message);
        }
    }

    /**
     * Create a new non-archiving session and activate it.
     * archive_prior=false keeps all existing sessions alive.
     */
    async function createNew() {
        try {
            const res = await fetch('/api/sessions', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ archive_prior: false }),
            });

            if (!res.ok) {
                console.error('[Sessions] Create failed:', res.status);
                return;
            }

            const newSession = await res.json();

            // Re-fetch the full session list so the new tab appears.
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

    /**
     * Full reset — wipe all progress.  Displays specific messages for known
     * error codes rather than a single generic toast.
     */
    async function resetAll() {
        if (!confirm('This will erase all your progress and start from the beginning. Are you sure?')) {
            return;
        }

        try {
            const res = await fetch('/api/sessions/reset', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
            });

            if (res.ok) {
                // Full page reload resets all module state, phase stepper, results panel, etc.
                window.location.reload();
                return;
            }

            // Map known status codes to user-friendly messages.
            if (res.status === 429) {
                const retryAfter = res.headers.get('Retry-After');
                const hint = retryAfter
                    ? ' Please wait ' + retryAfter + ' seconds before trying again.'
                    : ' Please wait a while before trying again.';
                Toast.show('You’ve reset too many times recently.' + hint, 'error');
            } else if (res.status === 401) {
                Toast.show('You must be signed in to reset your progress.', 'error');
            } else {
                Toast.show('Reset failed (error ' + res.status + '). Please try again.', 'error');
            }
        } catch (err) {
            console.error('[Sessions] Reset error:', err.message);
            Toast.show('Reset failed: ' + err.message, 'error');
        }
    }

    // ── Private helpers ─────────────────────────────────────────────────────

    /**
     * Render keyboard-accessible tab strip into #session-tabs.
     *
     * ARIA: container has role="tablist", each tab has role="tab".
     * Roving tabindex: active tab = 0, others = -1.
     * Arrow keys move focus within the strip (left/right/Home/End).
     */
    function _renderTabs(sessions) {
        const container = document.getElementById('session-tabs');
        if (!container) return;
        container.textContent = '';

        const tabs = [];

        for (const session of sessions) {
            const isActive = session.session_id === _activeSessionId;
            const isEditing = session.session_id === _editingSessionId;

            const tab = document.createElement('button');
            tab.className = 'session-tab' + (isActive ? ' session-tab--active' : '');
            tab.type = 'button';
            tab.setAttribute('role', 'tab');
            tab.setAttribute('aria-selected', String(isActive));
            tab.setAttribute('tabindex', isActive ? '0' : '-1');
            tab.dataset.sessionId = session.session_id;

            if (isEditing) {
                tab.classList.add('session-tab--editing');
                const input = _buildRenameInput(session);
                tab.appendChild(input);
            } else {
                const label = session.title || _formatSessionLabel(session);
                Sanitize.setText(tab, label);
            }

            tab.addEventListener('click', (e) => {
                // Don't activate while the rename input is focused inside this tab.
                if (e.target.tagName === 'INPUT') return;
                if (_editingSessionId === session.session_id) return;
                activate(session.session_id);
            });
            tab.addEventListener('dblclick', () => _beginRename(session.session_id));
            tab.addEventListener('keydown', (e) => _handleTabKeydown(e, session.session_id, tabs));

            container.appendChild(tab);
            tabs.push(tab);
        }

        // "New +" button at the end of the strip.
        const newBtn = document.createElement('button');
        newBtn.className = 'session-tab session-tab--new';
        newBtn.type = 'button';
        newBtn.textContent = '+';
        newBtn.title = 'New session';
        newBtn.setAttribute('aria-label', 'New session');
        newBtn.addEventListener('click', createNew);
        container.appendChild(newBtn);
    }

    /**
     * Render the bottom bar (#session-list) — only the "Start Over" button
     * lives here now that tabs are in the chat panel.
     */
    function _renderBottomBar() {
        const listEl = document.getElementById('session-list');
        if (!listEl) return;
        listEl.textContent = '';

        const resetBtn = document.createElement('button');
        resetBtn.className = 'session-btn session-btn--reset';
        resetBtn.type = 'button';
        resetBtn.textContent = 'Start Over';
        resetBtn.addEventListener('click', resetAll);
        listEl.appendChild(resetBtn);
    }

    /**
     * Build the rename <input> element for an editing tab.
     */
    function _buildRenameInput(session) {
        const currentLabel = session.title || _formatSessionLabel(session);
        const input = document.createElement('input');
        input.type = 'text';
        input.className = 'session-tab__rename-input';
        input.value = currentLabel;
        input.maxLength = 80;
        input.setAttribute('aria-label', 'Rename session');

        // Defer focus so the element is in the DOM first.
        requestAnimationFrame(() => {
            input.focus();
            input.select();
        });

        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                e.stopPropagation();
                _commitRename(session.session_id, input.value);
            } else if (e.key === 'Escape') {
                e.preventDefault();
                e.stopPropagation();
                _cancelRename();
            }
        });

        input.addEventListener('blur', () => {
            // Only commit on blur if still editing this session (Esc clears the flag).
            if (_editingSessionId === session.session_id) {
                _commitRename(session.session_id, input.value);
            }
        });

        // Prevent click on the input from triggering tab activation.
        input.addEventListener('click', (e) => e.stopPropagation());

        return input;
    }

    /**
     * Arrow-key / Home / End keyboard navigation within the tab strip.
     * Enter and F2 trigger inline rename.
     */
    function _handleTabKeydown(e, sessionId, tabElements) {
        const idx = tabElements.findIndex(t => t.dataset.sessionId === sessionId);
        if (idx === -1) return;

        if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
            e.preventDefault();
            const next = e.key === 'ArrowRight'
                ? (idx + 1) % tabElements.length
                : (idx - 1 + tabElements.length) % tabElements.length;
            tabElements[next].focus();
            tabElements[next].setAttribute('tabindex', '0');
            tabElements[idx].setAttribute('tabindex', '-1');
        } else if (e.key === 'Home') {
            e.preventDefault();
            tabElements[0].focus();
        } else if (e.key === 'End') {
            e.preventDefault();
            tabElements[tabElements.length - 1].focus();
        } else if (e.key === 'Enter' || e.key === 'F2') {
            e.preventDefault();
            _beginRename(sessionId);
        }
    }

    /**
     * Enter editing state for the given session.
     */
    function _beginRename(sessionId) {
        _editingSessionId = sessionId;
        _renderTabs(_sessions);
    }

    /**
     * Commit rename: send PATCH, update local state, re-render.
     */
    async function _commitRename(sessionId, rawValue) {
        // Clear editing state immediately to prevent double-commit on blur.
        _editingSessionId = null;

        const title = rawValue ? rawValue.trim() : '';
        if (!title) {
            // Empty — cancel silently.
            render(_sessions);
            return;
        }
        if (title.length > 80) {
            Toast.show('Session title must be 80 characters or fewer.', 'error');
            render(_sessions);
            return;
        }

        try {
            const res = await fetch('/api/sessions/' + encodeURIComponent(sessionId), {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ title }),
            });

            if (res.ok) {
                const updated = await res.json();
                // Update the local sessions array with the new title.
                _sessions = _sessions.map(s =>
                    s.session_id === sessionId
                        ? { ...s, title: updated.title || title }
                        : s
                );
            } else {
                console.error('[Sessions] Rename failed:', res.status);
                Toast.show('Rename failed. Please try again.', 'error');
            }
        } catch (err) {
            console.error('[Sessions] Rename error:', err.message);
            Toast.show('Rename failed: ' + err.message, 'error');
        }

        render(_sessions);
    }

    /**
     * Cancel rename — revert to the original label without an API call.
     */
    function _cancelRename() {
        _editingSessionId = null;
        render(_sessions);
    }

    /**
     * Derive a display label from session metadata when no title is set.
     */
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
        resetAll,
        // Exposed for E2E test convenience only — triggers rename mode programmatically.
        _beginRenameForTest: _beginRename,
    };
})();
