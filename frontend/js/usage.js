/* Transmutation Engine - LLM Call History Dialog */
'use strict';

/**
 * Usage module — manages the LLM call history dialog.
 *
 * Public API: Usage.init(), Usage.open(), Usage.close()
 *
 * Security: all user-data cells rendered via Sanitize.setText (never innerHTML).
 * Accessibility: role="dialog", aria-modal, focus-trap, Esc-close, focus-return.
 */
const Usage = (() => {
    // ---------------------------------------------------------------------------
    // State
    // ---------------------------------------------------------------------------
    let _nextCursor = null;
    let _hasMore = false;
    let _loading = false;
    let _initialized = false;
    let _keypressHandler = null;

    // ---------------------------------------------------------------------------
    // DOM helpers
    // ---------------------------------------------------------------------------
    const _el = id => document.getElementById(id);

    function _show(id)    { const e = _el(id); if (e) e.hidden = false; }
    function _hide(id)    { const e = _el(id); if (e) e.hidden = true; }
    function _setText(id, text) {
        const e = _el(id);
        if (e) Sanitize.setText(e, text);
    }

    // ---------------------------------------------------------------------------
    // Focus trap
    // ---------------------------------------------------------------------------
    const _FOCUSABLE = [
        'a[href]', 'button:not([disabled])', 'input:not([disabled])',
        'select:not([disabled])', 'textarea:not([disabled])',
        '[tabindex]:not([tabindex="-1"])',
    ].join(', ');

    function _trapFocus(e) {
        const dialog = _el('cost-dialog');
        if (!dialog) return;
        const focusable = Array.from(dialog.querySelectorAll(_FOCUSABLE));
        if (!focusable.length) return;
        const first = focusable[0];
        const last  = focusable[focusable.length - 1];
        if (e.key === 'Tab') {
            if (e.shiftKey) {
                if (document.activeElement === first) { e.preventDefault(); last.focus(); }
            } else {
                if (document.activeElement === last)  { e.preventDefault(); first.focus(); }
            }
        }
        if (e.key === 'Escape') {
            e.preventDefault();
            close();
        }
    }

    // ---------------------------------------------------------------------------
    // Timestamp formatting
    // ---------------------------------------------------------------------------
    function _formatDate(isoStr) {
        if (!isoStr) return '';
        try {
            const d = new Date(isoStr.replace(' ', 'T') + (isoStr.includes('+') ? '' : 'Z'));
            const mo = String(d.getUTCMonth() + 1).padStart(2, '0');
            const dy = String(d.getUTCDate()).padStart(2, '0');
            const hr = String(d.getUTCHours()).padStart(2, '0');
            const mn = String(d.getUTCMinutes()).padStart(2, '0');
            return `${mo}-${dy} ${hr}:${mn}`;
        } catch (_) {
            return '';
        }
    }

    function _formatCost(n) {
        return '$' + (typeof n === 'number' ? n.toFixed(4) : '0.0000');
    }

    function _formatTokens(n) {
        return typeof n === 'number' ? n.toLocaleString() : '0';
    }

    // ---------------------------------------------------------------------------
    // Rendering
    // ---------------------------------------------------------------------------
    function _renderRows(items) {
        const tbody = _el('cost-dialog-tbody');
        if (!tbody) return;
        items.forEach(item => {
            const tr = document.createElement('tr');

            const tdWhen = document.createElement('td');
            Sanitize.setText(tdWhen, _formatDate(item.created_at));
            tr.appendChild(tdWhen);

            const tdDesc = document.createElement('td');
            Sanitize.setText(tdDesc, item.description || '');
            tr.appendChild(tdDesc);

            const tdIn = document.createElement('td');
            tdIn.className = 'cost-dialog__num';
            Sanitize.setText(tdIn, _formatTokens(item.input_tokens));
            tr.appendChild(tdIn);

            const tdOut = document.createElement('td');
            tdOut.className = 'cost-dialog__num';
            Sanitize.setText(tdOut, _formatTokens(item.output_tokens));
            tr.appendChild(tdOut);

            const tdCost = document.createElement('td');
            tdCost.className = 'cost-dialog__num';
            Sanitize.setText(tdCost, _formatCost(item.cost_usd));
            tr.appendChild(tdCost);

            tbody.appendChild(tr);
        });
    }

    function _updateLoadMore() {
        const btn = _el('cost-dialog-load-more');
        if (!btn) return;
        if (_hasMore) {
            btn.hidden = false;
            btn.disabled = _loading;
        } else {
            btn.hidden = true;
        }
    }

    // ---------------------------------------------------------------------------
    // Data fetching
    // ---------------------------------------------------------------------------
    async function _fetchPage(cursor) {
        const url = cursor
            ? `/api/usage/llm-calls?limit=25&cursor=${encodeURIComponent(cursor)}`
            : '/api/usage/llm-calls?limit=25';
        const res = await fetch(url, { credentials: 'include' });
        if (!res.ok) {
            const body = await res.json().catch(() => ({}));
            throw new Error(body.detail || `HTTP ${res.status}`);
        }
        return res.json();
    }

    async function _loadFirstPage() {
        _loading = true;
        _show('cost-dialog-loading');
        _hide('cost-dialog-empty');
        _hide('cost-dialog-error');
        _hide('cost-dialog-load-more');

        // Clear old rows
        const tbody = _el('cost-dialog-tbody');
        if (tbody) tbody.textContent = '';

        try {
            const data = await _fetchPage(null);
            _hide('cost-dialog-loading');
            _nextCursor = data.next_cursor || null;
            _hasMore = Boolean(data.has_more);

            if (!data.items || data.items.length === 0) {
                _show('cost-dialog-empty');
            } else {
                _renderRows(data.items);
            }
            _updateLoadMore();
        } catch (err) {
            _hide('cost-dialog-loading');
            _setText('cost-dialog-error', 'Could not load call history. Please try again.');
            _show('cost-dialog-error');
            if (typeof Toast !== 'undefined') {
                Toast.show('Failed to load LLM call history', 'error');
            }
        } finally {
            _loading = false;
            _updateLoadMore();
        }
    }

    async function _loadNextPage() {
        if (_loading || !_hasMore || !_nextCursor) return;
        _loading = true;
        _updateLoadMore();

        try {
            const data = await _fetchPage(_nextCursor);
            _nextCursor = data.next_cursor || null;
            _hasMore = Boolean(data.has_more);
            if (data.items && data.items.length > 0) {
                _renderRows(data.items);
            }
            _updateLoadMore();
        } catch (err) {
            _setText('cost-dialog-error', 'Could not load more results. Please try again.');
            _show('cost-dialog-error');
            if (typeof Toast !== 'undefined') {
                Toast.show('Failed to load more LLM calls', 'error');
            }
        } finally {
            _loading = false;
            _updateLoadMore();
        }
    }

    // ---------------------------------------------------------------------------
    // Public: open / close
    // ---------------------------------------------------------------------------
    function open() {
        const dialog = _el('cost-dialog');
        const backdrop = _el('cost-dialog-backdrop');
        if (!dialog) return;

        dialog.hidden = false;
        backdrop.hidden = false;
        backdrop.removeAttribute('aria-hidden');
        dialog.setAttribute('aria-hidden', 'false');

        // Focus the close button initially
        const closeBtn = _el('cost-dialog-close');
        if (closeBtn) closeBtn.focus();

        // Attach key handler for focus-trap + Esc
        _keypressHandler = _trapFocus;
        document.addEventListener('keydown', _keypressHandler);

        // Update subtitle with current lifetime cost from cost-display text
        const costEl = _el('cost-display');
        const subtitle = _el('cost-dialog-subtitle');
        if (costEl && subtitle) {
            Sanitize.setText(subtitle, costEl.textContent || '');
        }

        _loadFirstPage();
    }

    function close() {
        const dialog = _el('cost-dialog');
        const backdrop = _el('cost-dialog-backdrop');
        if (!dialog) return;

        dialog.hidden = true;
        backdrop.hidden = true;
        backdrop.setAttribute('aria-hidden', 'true');
        dialog.setAttribute('aria-hidden', 'true');

        if (_keypressHandler) {
            document.removeEventListener('keydown', _keypressHandler);
            _keypressHandler = null;
        }

        // Return focus to trigger button (frontend-accessibility R3)
        const trigger = _el('cost-display');
        if (trigger) trigger.focus();
    }

    // ---------------------------------------------------------------------------
    // Public: init
    // ---------------------------------------------------------------------------
    function init() {
        if (_initialized) return;
        _initialized = true;

        // Open dialog when cost-display button is clicked
        const triggerBtn = _el('cost-display');
        if (triggerBtn) {
            triggerBtn.addEventListener('click', open);
        }

        // Close button inside dialog
        const closeBtn = _el('cost-dialog-close');
        if (closeBtn) {
            closeBtn.addEventListener('click', close);
        }

        // Backdrop click closes dialog
        const backdrop = _el('cost-dialog-backdrop');
        if (backdrop) {
            backdrop.addEventListener('click', close);
        }

        // "Load more" button
        const loadMoreBtn = _el('cost-dialog-load-more');
        if (loadMoreBtn) {
            loadMoreBtn.addEventListener('click', _loadNextPage);
        }
    }

    return { init, open, close };
})();
