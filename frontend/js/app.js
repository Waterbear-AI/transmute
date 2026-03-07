/* Transmutation Engine - Main Application */
'use strict';

const App = (() => {
    let _currentSessionId = null;
    let _logoutBound = false;

    function showApp(user) {
        document.getElementById('auth-overlay').hidden = true;
        document.getElementById('app').hidden = false;
        Sanitize.setText(document.getElementById('user-name'), user.name);

        if (!_logoutBound) {
            document.getElementById('logout-btn').addEventListener('click', handleLogout);
            document.getElementById('download-data-btn').addEventListener('click', () => handleDownload(user.user_id));
            _logoutBound = true;
        }
    }

    function showAuth() {
        document.getElementById('auth-overlay').hidden = false;
        document.getElementById('app').hidden = true;
        Auth.renderLoginForm(document.getElementById('auth-container'));
    }

    async function handleLogout() {
        _logoutBound = false;
        await Auth.logout();
    }

    function handleAuthChange(user) {
        if (user) {
            showApp(user);
            initMainApp(user);
        } else {
            showAuth();
        }
    }

    async function initMainApp(user) {
        Chat.init();

        try {
            const [sessionsRes, resultsRes] = await Promise.all([
                fetch('/api/sessions'),
                fetch('/api/results/' + encodeURIComponent(user.user_id))
            ]);

            if (sessionsRes.ok) {
                const body = await sessionsRes.json();
                const sessions = body.sessions || body;
                Sessions.render(sessions);
                if (sessions.length > 0) {
                    Sessions.activate(sessions[0].session_id);
                } else {
                    // Create first session automatically
                    await Sessions.createNew();
                }
            } else {
                // No sessions endpoint or error — create new
                await Sessions.createNew();
            }

            if (resultsRes.ok) {
                const results = await resultsRes.json();
                Results.update(results, user.current_phase);
            } else {
                // No results yet — show default orientation
                Results.update({}, user.current_phase || 'orientation');
            }
        } catch (err) {
            console.error('[App] Init failed:', err.message);
            Toast.show('Failed to load application data', 'error');
        }
    }

    function handleDownload(userId) {
        window.location.href = '/export/' + encodeURIComponent(userId);
    }

    function getCurrentSessionId() {
        return _currentSessionId;
    }

    function setCurrentSessionId(id) {
        _currentSessionId = id;
    }

    async function init() {
        Toast.init();
        Auth.setAuthChangeCallback(handleAuthChange);
        const user = await Auth.checkSession();
        if (user) {
            handleAuthChange(user);
        } else {
            showAuth();
        }
    }

    return { init, getCurrentSessionId, setCurrentSessionId };
})();

document.addEventListener('DOMContentLoaded', () => App.init());
