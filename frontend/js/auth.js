/* Transmutation Engine - Auth Module */
'use strict';

const Auth = (() => {
    let _currentUser = null;
    let _onAuthChange = null;

    function setAuthChangeCallback(cb) {
        _onAuthChange = cb;
    }

    function getCurrentUser() {
        return _currentUser;
    }

    async function checkSession() {
        try {
            const res = await fetch('/auth/me');
            if (res.ok) {
                _currentUser = await res.json();
                return _currentUser;
            }
        } catch (err) {
            console.warn('[Auth] Session check failed:', err.message);
        }
        _currentUser = null;
        return null;
    }

    async function login(email, password) {
        try {
            const res = await fetch('/auth/login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ email, password })
            });
            if (res.ok) {
                _currentUser = await res.json();
                if (_onAuthChange) _onAuthChange(_currentUser);
                return { ok: true, user: _currentUser };
            }
            const data = await res.json().catch(() => ({}));
            return { ok: false, error: data.detail || 'Login failed' };
        } catch (err) {
            return { ok: false, error: 'Network error. Please try again.' };
        }
    }

    async function register(name, email, password) {
        try {
            const res = await fetch('/auth/register', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name, email, password })
            });
            if (res.ok) {
                _currentUser = await res.json();
                if (_onAuthChange) _onAuthChange(_currentUser);
                return { ok: true, user: _currentUser };
            }
            const data = await res.json().catch(() => ({}));
            return { ok: false, error: data.detail || 'Registration failed' };
        } catch (err) {
            return { ok: false, error: 'Network error. Please try again.' };
        }
    }

    async function logout() {
        try {
            await fetch('/auth/logout', { method: 'POST' });
        } catch (err) {
            console.warn('[Auth] Logout request failed:', err.message);
        }
        _currentUser = null;
        if (_onAuthChange) _onAuthChange(null);
    }

    function _createInput(type, name, placeholder, autocomplete) {
        const input = document.createElement('input');
        input.type = type;
        input.name = name;
        input.placeholder = placeholder;
        input.required = true;
        input.autocomplete = autocomplete;
        return input;
    }

    function _createButton(text) {
        const btn = document.createElement('button');
        btn.type = 'submit';
        btn.textContent = text;
        return btn;
    }

    function _createToggle(text, linkText, onClick) {
        const div = document.createElement('div');
        div.className = 'auth-toggle';
        div.appendChild(document.createTextNode(text + ' '));
        const a = document.createElement('a');
        a.textContent = linkText;
        a.href = '#';
        a.addEventListener('click', (e) => {
            e.preventDefault();
            onClick();
        });
        div.appendChild(a);
        return div;
    }

    function renderLoginForm(container) {
        container.textContent = '';
        const form = document.createElement('form');
        form.className = 'auth-form';

        const h2 = document.createElement('h2');
        h2.textContent = 'Sign In';
        form.appendChild(h2);

        const errorEl = document.createElement('div');
        errorEl.className = 'auth-error';
        form.appendChild(errorEl);

        form.appendChild(_createInput('email', 'email', 'Email', 'email'));
        form.appendChild(_createInput('password', 'password', 'Password', 'current-password'));
        form.appendChild(_createButton('Sign In'));
        form.appendChild(_createToggle("Don't have an account?", 'Create Account', () => renderRegisterForm(container)));

        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            Sanitize.setText(errorEl, '');
            const result = await login(form.elements.email.value, form.elements.password.value);
            if (!result.ok) Sanitize.setText(errorEl, result.error);
        });

        container.appendChild(form);
    }

    function renderRegisterForm(container) {
        container.textContent = '';
        const form = document.createElement('form');
        form.className = 'auth-form';

        const h2 = document.createElement('h2');
        h2.textContent = 'Create Account';
        form.appendChild(h2);

        const errorEl = document.createElement('div');
        errorEl.className = 'auth-error';
        form.appendChild(errorEl);

        form.appendChild(_createInput('text', 'name', 'Name', 'name'));
        form.appendChild(_createInput('email', 'email', 'Email', 'email'));
        form.appendChild(_createInput('password', 'password', 'Password', 'new-password'));
        form.appendChild(_createButton('Create Account'));
        form.appendChild(_createToggle('Already have an account?', 'Sign In', () => renderLoginForm(container)));

        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            Sanitize.setText(errorEl, '');
            const result = await register(form.elements.name.value, form.elements.email.value, form.elements.password.value);
            if (!result.ok) Sanitize.setText(errorEl, result.error);
        });

        container.appendChild(form);
    }

    return {
        setAuthChangeCallback,
        getCurrentUser,
        checkSession,
        login,
        register,
        logout,
        renderLoginForm,
        renderRegisterForm
    };
})();
