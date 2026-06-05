/* Transmutation Engine - ContinuePrompt Component */
'use strict';

const ContinuePrompt = (() => {

    /**
     * Create a Continue button from an education.continue SSE event.
     * data: {label, message}
     *
     * Renders a single primary button. When the user clicks it (or activates
     * via Enter/Space — native <button> behavior), the button is removed from
     * the DOM and a `continue` control message is sent back to the agent via
     * Chat.sendMessage (which does NOT render a user-message bubble). The
     * message is JSON-encoded as {type:'continue', message} so that, like the
     * comprehension_answer payload, it is suppressed from the transcript on
     * reload instead of appearing as a raw "Yes, continue…" user bubble.
     * The button cannot be double-submitted because it removes itself on the
     * first activation.
     */
    function create(data) {
        data = data || {};

        const card = document.createElement('div');
        card.className = 'widget-card continue-prompt';
        card.setAttribute('role', 'group');
        card.setAttribute('aria-label', 'Continue');

        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'continue-prompt-btn';
        // Agent-supplied text is untrusted — render as a text node, never innerHTML.
        Sanitize.setText(btn, data.label || 'Continue');

        btn.addEventListener('click', () => {
            if (btn.disabled) return;
            btn.disabled = true;

            const message = data.message || 'continue';
            const sessionId = (typeof App !== 'undefined') ? App.getCurrentSessionId() : null;

            // Remove the button immediately so it disappears on click and
            // cannot be activated twice.
            card.remove();

            if (sessionId && typeof Chat !== 'undefined') {
                // Structured control payload (not raw text) so history
                // rehydration can suppress it — see _CONTROL_MESSAGE_TYPES in
                // chat.js. The `message` field carries the natural-language
                // confirmation the agent reads to proceed.
                Chat.sendMessage(sessionId, JSON.stringify({
                    type: 'continue',
                    message: message,
                }));
            }
        });

        card.appendChild(btn);
        return card;
    }

    return { create };
})();
