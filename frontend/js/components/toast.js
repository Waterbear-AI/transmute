/* Transmutation Engine - Toast Notification System */
'use strict';

const Toast = (() => {
    const MAX_VISIBLE = 3;
    const TIMERS = { success: 3000, warning: 5000, error: null };
    const ICONS = { success: '\u2713', warning: '\u26A0', error: '\u2717' };

    let _container = null;
    let _toasts = [];

    function init() {
        if (_container) return;
        _container = document.createElement('div');
        _container.className = 'toast-container';
        _container.setAttribute('aria-live', 'polite');
        _container.setAttribute('aria-atomic', 'false');
        document.body.appendChild(_container);
    }

    /**
     * Show a toast notification.
     * @param {string} message - Text to display
     * @param {'success'|'warning'|'error'} type - Toast type
     * @param {Object} [options] - Optional config
     * @param {Function} [options.onRetry] - Retry callback (shows Retry button)
     */
    function show(message, type, options) {
        if (!_container) init();
        type = type || 'success';
        options = options || {};

        const el = document.createElement('div');
        el.className = 'toast toast--' + type;
        el.setAttribute('role', 'alert');

        // Icon
        const icon = document.createElement('span');
        icon.className = 'toast__icon';
        icon.textContent = ICONS[type] || '';
        el.appendChild(icon);

        // Content wrapper
        const content = document.createElement('div');
        content.className = 'toast__content';

        const text = document.createElement('span');
        text.className = 'toast__message';
        Sanitize.setText(text, message);
        content.appendChild(text);

        // Retry button for errors
        if (options.onRetry && typeof options.onRetry === 'function') {
            const retryBtn = document.createElement('button');
            retryBtn.className = 'toast__retry';
            retryBtn.textContent = 'Retry';
            retryBtn.addEventListener('click', () => {
                _dismiss(el);
                options.onRetry();
            });
            content.appendChild(retryBtn);
        }

        el.appendChild(content);

        // Dismiss button for errors (and manual dismiss for all)
        const dismissBtn = document.createElement('button');
        dismissBtn.className = 'toast__dismiss';
        dismissBtn.textContent = '\u00D7';
        dismissBtn.setAttribute('aria-label', 'Dismiss');
        dismissBtn.addEventListener('click', () => _dismiss(el));
        el.appendChild(dismissBtn);

        // Track toast
        const entry = { el, timerId: null };
        _toasts.push(entry);

        // Enforce max visible — remove oldest
        while (_toasts.length > MAX_VISIBLE) {
            _dismiss(_toasts[0].el);
        }

        _container.appendChild(el);

        // Trigger slide-in animation
        requestAnimationFrame(() => {
            el.classList.add('toast--visible');
        });

        // Auto-dismiss timer
        const duration = TIMERS[type];
        if (duration) {
            entry.timerId = setTimeout(() => _dismiss(el), duration);
        }

        return el;
    }

    function _dismiss(el) {
        const idx = _toasts.findIndex(t => t.el === el);
        if (idx === -1) return;

        const entry = _toasts[idx];
        if (entry.timerId) clearTimeout(entry.timerId);
        _toasts.splice(idx, 1);

        el.classList.remove('toast--visible');
        el.classList.add('toast--exiting');
        el.addEventListener('transitionend', () => el.remove(), { once: true });
        // Fallback removal if transitionend doesn't fire
        setTimeout(() => { if (el.parentNode) el.remove(); }, 400);
    }

    return { init, show };
})();
