/* Transmutation Engine - Tier Progress Component */
'use strict';

/**
 * Renders a "Tier X of 3" affordance for the tiered assessment flow
 * (transmute_core -> awareness_core -> awareness_deepdive -> complete).
 * Tier is always server-authoritative (assessment_tier column /
 * assessment_state.assessment_tier) — this component only displays
 * whatever tier string it is given, never infers or advances it.
 */
const TierProgress = (() => {
    // Ordered so unknown/complete states can compute a sensible position.
    const TIERS = [
        { id: 'transmute_core', label: 'Transmutation Capacity', step: 1 },
        { id: 'awareness_core', label: 'Awareness Core', step: 2 },
        { id: 'awareness_deepdive', label: 'Adaptive Deep-Dive', step: 3 },
        { id: 'complete', label: 'Complete', step: 4 }
    ];
    const TOTAL_ASSESSMENT_TIERS = 3; // transmute_core, awareness_core, awareness_deepdive

    /**
     * @param {string|null|undefined} tier - current assessment_tier value.
     * @returns {HTMLElement}
     */
    function create(tier) {
        const entry = TIERS.find(t => t.id === tier);

        const wrap = document.createElement('div');
        wrap.className = 'tier-progress';
        wrap.setAttribute('role', 'status');

        const label = document.createElement('span');
        label.className = 'tier-progress__label';

        if (!entry) {
            Sanitize.setText(label, 'Assessment in progress');
        } else if (entry.id === 'complete') {
            Sanitize.setText(label, 'All tiers complete');
        } else {
            Sanitize.setText(
                label,
                'Tier ' + entry.step + ' of ' + TOTAL_ASSESSMENT_TIERS + ': ' + entry.label
            );
        }
        wrap.appendChild(label);

        return wrap;
    }

    return { create };
})();
