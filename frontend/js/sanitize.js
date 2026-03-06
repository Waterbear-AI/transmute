/* Transmutation Engine - XSS Sanitization Utilities
 *
 * XSS Prevention Strategy:
 * 1. Default: Use textContent for all plain text insertions (zero risk)
 * 2. When HTML formatting is needed: sanitizeHTML() strips all non-allowlisted
 *    tags and attributes using DOMParser + manual rebuild
 * 3. Never use innerHTML with unsanitized agent output
 * 4. CSP considerations: No inline scripts, styles from app.css only
 */
'use strict';

const Sanitize = (() => {
    // Allowlisted tags for formatted agent content
    const ALLOWED_TAGS = new Set([
        'b', 'i', 'em', 'strong', 'code', 'pre', 'br', 'p',
        'ul', 'ol', 'li', 'a', 'span', 'blockquote', 'h3', 'h4'
    ]);

    // Allowlisted attributes per tag
    const ALLOWED_ATTRS = {
        'a': ['href', 'title'],
        'code': ['class'],
        'pre': ['class'],
        'span': ['class']
    };

    /**
     * Set text content safely. This is the preferred method for all
     * agent-generated plain text. Never interprets HTML.
     */
    function setText(element, text) {
        element.textContent = text;
    }

    /**
     * Create a text node from untrusted content.
     */
    function textNode(text) {
        return document.createTextNode(text);
    }

    /**
     * Sanitize HTML string, keeping only allowlisted tags and attributes.
     * Returns a DocumentFragment ready for insertion via appendChild.
     */
    function sanitizeHTML(dirty) {
        if (!dirty || typeof dirty !== 'string') {
            return document.createDocumentFragment();
        }

        const parser = new DOMParser();
        const doc = parser.parseFromString(dirty, 'text/html');
        const fragment = document.createDocumentFragment();

        function cleanNode(node) {
            if (node.nodeType === Node.TEXT_NODE) {
                return document.createTextNode(node.textContent);
            }

            if (node.nodeType !== Node.ELEMENT_NODE) {
                return null;
            }

            const tagName = node.tagName.toLowerCase();

            if (!ALLOWED_TAGS.has(tagName)) {
                // Strip tag but keep children
                const frag = document.createDocumentFragment();
                for (const child of node.childNodes) {
                    const cleaned = cleanNode(child);
                    if (cleaned) frag.appendChild(cleaned);
                }
                return frag;
            }

            const el = document.createElement(tagName);

            // Copy only allowlisted attributes
            const allowedAttrs = ALLOWED_ATTRS[tagName] || [];
            for (const attrName of allowedAttrs) {
                const val = node.getAttribute(attrName);
                if (val === null) continue;

                // Extra safety: block javascript: URLs in href
                if (attrName === 'href') {
                    const lower = val.trim().toLowerCase();
                    if (lower.startsWith('javascript:') || lower.startsWith('data:')) {
                        continue;
                    }
                }
                el.setAttribute(attrName, val);
            }

            // Force external links to open in new tab safely
            if (tagName === 'a') {
                el.setAttribute('target', '_blank');
                el.setAttribute('rel', 'noopener noreferrer');
            }

            for (const child of node.childNodes) {
                const cleaned = cleanNode(child);
                if (cleaned) el.appendChild(cleaned);
            }

            return el;
        }

        for (const child of doc.body.childNodes) {
            const cleaned = cleanNode(child);
            if (cleaned) fragment.appendChild(cleaned);
        }

        return fragment;
    }

    /**
     * Sanitize HTML and return as an HTML string (for cases where innerHTML
     * is needed, e.g., updating a container's full content).
     */
    function sanitizeToString(dirty) {
        const fragment = sanitizeHTML(dirty);
        const temp = document.createElement('div');
        temp.appendChild(fragment);
        return temp.innerHTML;
    }

    /**
     * Escape a string for safe embedding in HTML attributes.
     */
    function escapeAttr(str) {
        const div = document.createElement('div');
        div.appendChild(document.createTextNode(str));
        return div.innerHTML;
    }

    return {
        setText,
        textNode,
        sanitizeHTML,
        sanitizeToString,
        escapeAttr,
        ALLOWED_TAGS
    };
})();
