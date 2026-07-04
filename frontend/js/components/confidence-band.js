/* Transmutation Engine - Confidence Band Component */
'use strict';

/**
 * Renders an honest, plain-language confidence indicator for early/partial
 * results (Barnum-effect mitigation — see spec.md B5.3). Used by the
 * Tier-1 early transmute result card (Results.renderEarlyResult) so users
 * understand a "medium"/"low" confidence result is a genuine early read,
 * not a hedge-free verdict.
 */
const ConfidenceBand = (() => {
    const LEVELS = {
        high: { label: 'High confidence', color: 'var(--color-success)' },
        medium: { label: 'Medium confidence', color: 'var(--color-warning)' },
        low: { label: 'Low confidence', color: 'var(--color-text-muted)' }
    };

    /**
     * @param {string|null|undefined} level - "high" | "medium" | "low" (any other
     *   value, or a missing value, renders as a neutral "Confidence: unknown" badge
     *   rather than throwing — server-authoritative but must degrade gracefully).
     * @param {string} [reason] - Honest plain-language explanation (e.g.
     *   "Based on ~18 core answers; a few more will sharpen it.").
     * @returns {HTMLElement} a self-contained band element.
     */
    function create(level, reason) {
        const info = LEVELS[level] || { label: 'Confidence: unknown', color: 'var(--color-text-muted)' };

        const band = document.createElement('div');
        band.className = 'confidence-band';
        band.setAttribute('role', 'status');
        band.style.borderLeftColor = info.color;

        const badge = document.createElement('span');
        badge.className = 'confidence-band__badge';
        badge.style.color = info.color;
        Sanitize.setText(badge, info.label);
        band.appendChild(badge);

        if (reason) {
            const reasonEl = document.createElement('span');
            reasonEl.className = 'confidence-band__reason';
            Sanitize.setText(reasonEl, reason);
            band.appendChild(reasonEl);
        }

        return band;
    }

    return { create };
})();
