/* Transmutation Engine - Quadrant Chart Component */
'use strict';

const QuadrantChart = (() => {
    const ARCHETYPES = [
        { name: 'Transmuter', f: 1, a: 1, color: 'rgba(34, 197, 94, 0.12)' },
        { name: 'Absorber', f: 1, a: -1, color: 'rgba(59, 130, 246, 0.12)' },
        { name: 'Magnifier', f: -1, a: 1, color: 'rgba(249, 115, 22, 0.12)' },
        { name: 'Extractor', f: -1, a: -1, color: 'rgba(168, 85, 247, 0.12)' }
    ];

    const CONDUIT_THRESHOLD = 0.15;
    const PADDING = 40;
    const LABEL_FONT = '11px -apple-system, BlinkMacSystemFont, sans-serif';
    const TITLE_FONT = '12px -apple-system, BlinkMacSystemFont, sans-serif';

    /**
     * Render a quadrant chart into a container element.
     * @param {HTMLElement} containerEl - DOM element to render into
     * @param {Object|null} flowData - flow_data from profile snapshot
     */
    function render(containerEl, flowData) {
        containerEl.textContent = '';

        if (!flowData || !flowData.levels || flowData.levels.length === 0) {
            const placeholder = document.createElement('div');
            placeholder.className = 'quadrant-chart-placeholder';
            Sanitize.setText(placeholder, 'Flow data not yet computed. Complete assessment to see your quadrant position.');
            containerEl.appendChild(placeholder);
            return;
        }

        const wrapper = document.createElement('div');
        wrapper.className = 'quadrant-chart-wrapper';

        const canvas = document.createElement('canvas');
        canvas.className = 'quadrant-chart-canvas';
        canvas.setAttribute('role', 'img');
        canvas.setAttribute('aria-label', 'Quadrant chart showing your filtering and amplification position');
        wrapper.appendChild(canvas);

        // Tooltip element
        const tooltip = document.createElement('div');
        tooltip.className = 'quadrant-chart-tooltip';
        tooltip.hidden = true;
        wrapper.appendChild(tooltip);

        containerEl.appendChild(wrapper);

        // Compute user position from latest level
        const latest = flowData.levels[flowData.levels.length - 1];
        const userF = latest.filtering || 0;
        const userA = latest.amplification || 0;

        function draw() {
            const rect = wrapper.getBoundingClientRect();
            const size = Math.max(Math.floor(rect.width), 250);
            const dpr = window.devicePixelRatio || 1;

            canvas.width = size * dpr;
            canvas.height = size * dpr;
            canvas.style.width = size + 'px';
            canvas.style.height = size + 'px';

            const ctx = canvas.getContext('2d');
            ctx.scale(dpr, dpr);

            _drawChart(ctx, size, userF, userA);
        }

        draw();

        // Resize handler
        let resizeTimer;
        const onResize = () => {
            clearTimeout(resizeTimer);
            resizeTimer = setTimeout(draw, 150);
        };
        window.addEventListener('resize', onResize);

        // Hover tooltip
        canvas.addEventListener('mousemove', (e) => {
            const rect = canvas.getBoundingClientRect();
            const x = e.clientX - rect.left;
            const y = e.clientY - rect.top;
            const chartSize = rect.width;
            const cx = chartSize / 2;
            const cy = chartSize / 2;
            const plotSize = chartSize - PADDING * 2;

            // Check if near user dot (within 15px)
            const dotX = cx + (userA / 1.2) * (plotSize / 2);
            const dotY = cy - (userF / 1.2) * (plotSize / 2);
            const dist = Math.sqrt((x - dotX) ** 2 + (y - dotY) ** 2);

            if (dist < 15) {
                const archetype = _getArchetype(userF, userA);
                Sanitize.setText(tooltip, archetype + ' (F: ' + userF.toFixed(2) + ', A: ' + userA.toFixed(2) + ')');
                tooltip.style.left = (x + 12) + 'px';
                tooltip.style.top = (y - 8) + 'px';
                tooltip.hidden = false;
            } else {
                tooltip.hidden = true;
            }
        });

        canvas.addEventListener('mouseleave', () => {
            tooltip.hidden = true;
        });

        // Store cleanup reference
        containerEl._quadrantCleanup = () => {
            window.removeEventListener('resize', onResize);
        };
    }

    function _drawChart(ctx, size, userF, userA) {
        const cx = size / 2;
        const cy = size / 2;
        const plotSize = size - PADDING * 2;
        const halfPlot = plotSize / 2;

        // Clear
        ctx.clearRect(0, 0, size, size);

        // Draw quadrant backgrounds
        for (const arch of ARCHETYPES) {
            ctx.fillStyle = arch.color;
            const x = arch.a > 0 ? cx : cx - halfPlot;
            const y = arch.f > 0 ? cy - halfPlot : cy;
            ctx.fillRect(x, y, halfPlot, halfPlot);
        }

        // Draw axes
        ctx.strokeStyle = 'rgba(148, 163, 184, 0.4)';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(PADDING, cy);
        ctx.lineTo(size - PADDING, cy);
        ctx.moveTo(cx, PADDING);
        ctx.lineTo(cx, size - PADDING);
        ctx.stroke();

        // Axis labels
        ctx.fillStyle = 'rgba(148, 163, 184, 0.7)';
        ctx.font = LABEL_FONT;
        ctx.textAlign = 'center';
        ctx.fillText('\u2190 Less Amplification | More Amplification \u2192', cx, size - 8);
        ctx.save();
        ctx.translate(12, cy);
        ctx.rotate(-Math.PI / 2);
        ctx.fillText('\u2190 Less Filtering | More Filtering \u2192', 0, 0);
        ctx.restore();

        // Archetype labels
        ctx.font = TITLE_FONT;
        ctx.fillStyle = 'rgba(228, 228, 231, 0.6)';
        ctx.textAlign = 'center';
        ctx.fillText('Absorber', cx - halfPlot / 2, cy - halfPlot + 16);
        ctx.fillText('Transmuter', cx + halfPlot / 2, cy - halfPlot + 16);
        ctx.fillText('Extractor', cx - halfPlot / 2, cy + halfPlot - 8);
        ctx.fillText('Magnifier', cx + halfPlot / 2, cy + halfPlot - 8);

        // Conduit zone circle at origin
        ctx.beginPath();
        ctx.arc(cx, cy, CONDUIT_THRESHOLD * halfPlot / 1.2, 0, Math.PI * 2);
        ctx.strokeStyle = 'rgba(148, 163, 184, 0.3)';
        ctx.lineWidth = 1;
        ctx.setLineDash([3, 3]);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillStyle = 'rgba(148, 163, 184, 0.4)';
        ctx.font = LABEL_FONT;
        ctx.fillText('Conduit', cx, cy + CONDUIT_THRESHOLD * halfPlot / 1.2 + 12);

        // User position dot
        const dotX = cx + (userA / 1.2) * halfPlot;
        const dotY = cy - (userF / 1.2) * halfPlot;

        // Glow
        ctx.beginPath();
        ctx.arc(dotX, dotY, 10, 0, Math.PI * 2);
        ctx.fillStyle = 'rgba(99, 102, 241, 0.3)';
        ctx.fill();

        // Dot
        ctx.beginPath();
        ctx.arc(dotX, dotY, 6, 0, Math.PI * 2);
        ctx.fillStyle = '#6366f1';
        ctx.fill();
        ctx.strokeStyle = '#fff';
        ctx.lineWidth = 1.5;
        ctx.stroke();

        // User archetype label
        const archetype = _getArchetype(userF, userA);
        ctx.fillStyle = '#e4e4e7';
        ctx.font = TITLE_FONT;
        ctx.textAlign = 'left';
        ctx.fillText(archetype, dotX + 12, dotY + 4);
    }

    function _getArchetype(f, a) {
        if (Math.abs(f) < CONDUIT_THRESHOLD && Math.abs(a) < CONDUIT_THRESHOLD) return 'Conduit';
        if (f >= 0 && a >= 0) return 'Transmuter';
        if (f >= 0 && a < 0) return 'Absorber';
        if (f < 0 && a >= 0) return 'Magnifier';
        return 'Extractor';
    }

    return { render };
})();
