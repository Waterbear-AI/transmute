/* Transmutation Engine - Main Application */
'use strict';

const App = (() => {
    let _currentSessionId = null;

    function showApp(user) {
        document.getElementById('auth-overlay').hidden = true;
        document.getElementById('app').hidden = false;
        Sanitize.setText(document.getElementById('user-name'), user.name);
        document.getElementById('logout-btn').addEventListener('click', handleLogout);
    }

    function showAuth() {
        document.getElementById('auth-overlay').hidden = false;
        document.getElementById('app').hidden = true;
        Auth.renderLoginForm(document.getElementById('auth-container'));
    }

    async function handleLogout() {
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
        // Load sessions and results in parallel
        try {
            const [sessionsRes, resultsRes] = await Promise.all([
                fetch('/sessions'),
                fetch('/api/results/' + encodeURIComponent(user.user_id))
            ]);

            if (sessionsRes.ok) {
                const sessions = await sessionsRes.json();
                Sessions.render(sessions);
                if (sessions.length > 0) {
                    Sessions.activate(sessions[0].session_id);
                }
            }

            if (resultsRes.ok) {
                const results = await resultsRes.json();
                Results.update(results, user.current_phase);
            }
        } catch (err) {
            console.error('[App] Init failed:', err.message);
        }
    }

    function getCurrentSessionId() {
        return _currentSessionId;
    }

    function setCurrentSessionId(id) {
        _currentSessionId = id;
    }

    async function init() {
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
