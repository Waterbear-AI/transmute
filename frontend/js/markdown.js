/* Transmutation Engine - Shared Markdown Rendering Utility
 *
 * Extracted from chat.js so chat.js and results.js render agent-authored
 * markdown identically. Depends on Sanitize (sanitize.js) for XSS-safe
 * output — load order is sanitize.js -> markdown.js -> chat.js/results.js.
 *
 * All markdown text handled here is model/agent output and is treated as
 * untrusted: toHTML() only ever returns markup built from the allowlisted
 * transform below, and render() inserts it via Sanitize.sanitizeHTML(),
 * never raw innerHTML.
 */
'use strict';

const Markdown = (() => {
    /**
     * Split a markdown table row into trimmed cell strings, dropping the
     * outer leading/trailing pipes (e.g. "| a | b |" -> ["a", "b"]).
     */
    function _splitTableRow(line) {
        return line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|')
            .map(cell => cell.trim());
    }

    /**
     * Convert GFM tables to <table> HTML. A table is a header row, a
     * delimiter row (pipes + dashes, e.g. "|---|---|"), and zero or more
     * body rows — all lines starting with "|". Non-table lines pass through
     * unchanged. Each table collapses to a single line so the downstream
     * newline->br pass leaves it alone.
     */
    function _tablesToHTML(text) {
        const isRow = line => /^\s*\|.*\|\s*$/.test(line);
        const isDelim = line => /^\s*\|(?:\s*:?-+:?\s*\|)+\s*$/.test(line);
        const lines = text.split('\n');
        const out = [];
        let i = 0;
        while (i < lines.length) {
            if (isRow(lines[i]) && i + 1 < lines.length && isDelim(lines[i + 1])) {
                const header = _splitTableRow(lines[i]);
                i += 2; // consume header + delimiter
                const body = [];
                while (i < lines.length && isRow(lines[i]) && !isDelim(lines[i])) {
                    body.push(_splitTableRow(lines[i]));
                    i += 1;
                }
                const thead = '<thead><tr>' +
                    header.map(c => '<th>' + c + '</th>').join('') + '</tr></thead>';
                const tbody = '<tbody>' + body.map(row =>
                    '<tr>' + row.map(c => '<td>' + c + '</td>').join('') + '</tr>'
                ).join('') + '</tbody>';
                out.push('<table>' + thead + tbody + '</table>');
            } else {
                out.push(lines[i]);
                i += 1;
            }
        }
        return out.join('\n');
    }

    /**
     * Convert basic markdown to HTML. The output is then run through
     * Sanitize.sanitizeHTML() so only allowlisted tags survive.
     */
    function toHTML(text) {
        let html = text;
        // Code blocks (``` ... ```)
        html = html.replace(/```(\w*)\n?([\s\S]*?)```/g, '<pre><code>$2</code></pre>');
        // Inline code
        html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
        // Headers (#, ##, ###, ####). Longest fences first so e.g. "### x"
        // is not matched by the "## " rule (## requires a space after, which
        // "###" lacks) — order is belt-and-suspenders.
        html = html.replace(/^#### (.+)$/gm, '<h4>$1</h4>');
        html = html.replace(/^### (.+)$/gm, '<h3>$1</h3>');
        html = html.replace(/^## (.+)$/gm, '<h2>$1</h2>');
        html = html.replace(/^# (.+)$/gm, '<h1>$1</h1>');
        // Horizontal rules
        html = html.replace(/^---+$/gm, '<br>');
        // Blockquotes: collapse consecutive lines starting with ">" into a
        // single <blockquote>. Inner content (bold, lists) is handled by the
        // passes below, so this must run before bold/list processing.
        html = html.replace(/(?:^|\n)((?:>[^\n]*(?:\n|$))+)/g, (_, block) => {
            const inner = block.replace(/\n+$/, '').split('\n')
                .map(line => line.replace(/^>\s?/, ''))
                .join('\n');
            // Keep the closing tag on its own line so the list/paragraph passes
            // below don't swallow "</blockquote>" into the final <li>.
            return '\n<blockquote>\n' + inner + '\n</blockquote>\n';
        });
        // Bold + italic
        html = html.replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>');
        // Bold
        html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
        // Italic
        html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');
        // GFM tables: a header row, a |---|---| delimiter, then body rows.
        // Runs after inline formatting so bold/code inside cells is already
        // converted, and before the list/paragraph passes so the table's
        // internal newlines aren't turned into <br>. Each matched block is
        // collapsed to a single-line <table>, leaving non-table lines intact.
        html = _tablesToHTML(html);
        // Unordered lists (consecutive lines starting with - )
        html = html.replace(/(?:^|\n)((?:- .+\n?)+)/g, (_, block) => {
            const items = block.trim().split('\n').map(line =>
                '<li>' + line.replace(/^- /, '') + '</li>'
            ).join('');
            return '<ul>' + items + '</ul>';
        });
        // Ordered lists (consecutive lines starting with N. )
        html = html.replace(/(?:^|\n)((?:\d+\. .+\n?)+)/g, (_, block) => {
            const items = block.trim().split('\n').map(line =>
                '<li>' + line.replace(/^\d+\. /, '') + '</li>'
            ).join('');
            return '<ol>' + items + '</ol>';
        });
        // Paragraphs: double newlines become <p> breaks
        html = html.replace(/\n{2,}/g, '</p><p>');
        // Single newlines become <br>
        html = html.replace(/\n/g, '<br>');
        html = '<p>' + html + '</p>';
        // Clean up empty paragraphs
        html = html.replace(/<p>\s*<\/p>/g, '');
        return html;
    }

    /**
     * Render `text` as sanitized markdown into `el`, replacing its current
     * content. Convenience wrapper around toHTML() + Sanitize.sanitizeHTML()
     * for the common "clear and set" case (chat message bubbles, journal
     * content bodies).
     */
    function render(el, text) {
        const html = toHTML(text);
        el.textContent = '';
        el.appendChild(Sanitize.sanitizeHTML(html));
    }

    return {
        toHTML,
        render
    };
})();
