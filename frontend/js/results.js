/* Transmutation Engine - Results Panel Module */
'use strict';

const Results = (() => {
    const TABS = [
        { id: 'orientation', label: 'Orientation', color: 'var(--phase-orientation)', dataKey: null },
        { id: 'assessment', label: 'Assessment', color: 'var(--phase-assessment)', dataKey: 'assessment_state' },
        { id: 'profile', label: 'Profile', color: 'var(--phase-profile)', dataKey: 'profile_snapshots' },
        { id: 'education', label: 'Education', color: 'var(--phase-education)', dataKey: 'education_progress' },
        { id: 'development', label: 'Development', color: 'var(--phase-development)', dataKey: 'development_roadmap' },
        { id: 'reassessment', label: 'Reassessment', color: 'var(--phase-reassessment)', dataKey: 'comparison_snapshots' },
        { id: 'graduation', label: 'Graduation', color: 'var(--phase-graduation)', dataKey: 'graduation_data' }
    ];

    let _currentPhase = 'orientation';
    let _activeTab = 'orientation';
    let _resultsData = {};

    /**
     * Update the results panel with data from /api/results/{user_id}.
     * Maps API response keys to internal data keys used by tabs.
     */
    function update(data, currentPhase) {
        const d = data || {};
        _resultsData = {};

        // Map API response to internal data keys
        if (d.assessment && d.assessment.exists) _resultsData.assessment_state = d.assessment;
        if (d.latest_profile) _resultsData.profile_snapshots = d.latest_profile;
        if (d.education && d.education.exists) _resultsData.education_progress = d.education;
        if (d.development && d.development.has_roadmap) _resultsData.development_roadmap = d.development;
        if (d.graduation && d.graduation.exists) _resultsData.graduation_data = d.graduation;
        if (d.check_ins && d.check_ins.count > 0) _resultsData.comparison_snapshots = d.check_ins;

        // Also accept pre-mapped data from SSE (passthrough)
        if (d.assessment_state) _resultsData.assessment_state = d.assessment_state;
        if (d.profile_snapshots) _resultsData.profile_snapshots = d.profile_snapshots;
        if (d.education_progress) _resultsData.education_progress = d.education_progress;
        if (d.development_roadmap) _resultsData.development_roadmap = d.development_roadmap;
        if (d.graduation_data) _resultsData.graduation_data = d.graduation_data;
        if (d.comparison_snapshots) _resultsData.comparison_snapshots = d.comparison_snapshots;

        _currentPhase = currentPhase || 'orientation';
        _renderTabs();
        _switchTab(_currentPhase);
    }

    /**
     * Handle phase.transition SSE event.
     */
    function handlePhaseTransition(from, to) {
        _currentPhase = to;
        _renderTabs();
        _switchTab(to);
    }

    /**
     * Handle domain SSE events that update tab content.
     */
    function handleSSEEvent(eventType, data) {
        switch (eventType) {
            case 'assessment.progress':
                _resultsData.assessment_state = data;
                if (_activeTab === 'assessment') _renderTabContent('assessment');
                // Ensure tab is visible
                _renderTabs();
                break;

            case 'profile.snapshot':
                _resultsData.profile_snapshots = data;
                if (_activeTab === 'profile') _renderTabContent('profile');
                _renderTabs();
                break;

            case 'education.progress':
                _resultsData.education_progress = data;
                if (_activeTab === 'education') _renderTabContent('education');
                _renderTabs();
                break;

            case 'education.comprehension':
                // Comprehension events carry score updates — merge into education data
                if (_resultsData.education_progress && data.progress) {
                    _resultsData.education_progress.progress = data.progress;
                    _resultsData.education_progress.summary = data.summary || _resultsData.education_progress.summary;
                }
                if (_activeTab === 'education') _renderTabContent('education');
                break;

            case 'development.roadmap':
            case 'development.practice':
                _resultsData.development_roadmap = data;
                if (_activeTab === 'development') _renderTabContent('development');
                _renderTabs();
                break;

            case 'graduation.readiness':
            case 'graduation.complete':
                _resultsData.graduation_data = data;
                if (_activeTab === 'graduation') _renderTabContent('graduation');
                _renderTabs();
                break;

            case 'checkin.complete':
                _resultsData.comparison_snapshots = data;
                if (_activeTab === 'reassessment') _renderTabContent('reassessment');
                _renderTabs();
                break;
        }
    }

    function _renderTabs() {
        const tabsEl = document.getElementById('results-tabs');
        tabsEl.textContent = '';

        for (const tab of TABS) {
            if (!_isTabVisible(tab)) continue;

            const btn = document.createElement('button');
            btn.className = 'results-tab';
            btn.setAttribute('role', 'tab');
            btn.setAttribute('aria-selected', tab.id === _activeTab ? 'true' : 'false');

            if (tab.id === _activeTab) {
                btn.classList.add('results-tab--active');
            }

            // Phase-colored dot
            const dot = document.createElement('span');
            dot.className = 'results-tab__dot';
            dot.style.backgroundColor = tab.color;
            if (tab.id === _currentPhase) {
                dot.style.display = 'block';
            }
            btn.appendChild(dot);

            btn.appendChild(Sanitize.textNode(tab.label));

            btn.addEventListener('click', () => _switchTab(tab.id));
            tabsEl.appendChild(btn);
        }
    }

    function _isTabVisible(tab) {
        // Orientation: visible when phase is orientation
        if (tab.id === 'orientation') {
            return _currentPhase === 'orientation';
        }
        // Other tabs: visible when their data exists
        if (tab.dataKey) {
            return !!_resultsData[tab.dataKey];
        }
        return false;
    }

    function _switchTab(tabId) {
        _activeTab = tabId;
        _renderTabs();
        _renderTabContent(tabId);
    }

    function _renderTabContent(tabId) {
        const contentEl = document.getElementById('results-content');
        contentEl.textContent = '';

        switch (tabId) {
            case 'orientation':
                _renderOrientation(contentEl);
                break;
            case 'assessment':
                _renderAssessment(contentEl);
                break;
            case 'profile':
                _renderProfile(contentEl);
                break;
            case 'education':
                _renderEducation(contentEl);
                break;
            case 'development':
                _renderDevelopment(contentEl);
                break;
            case 'reassessment':
                _renderReassessment(contentEl);
                break;
            case 'graduation':
                _renderGraduation(contentEl);
                break;
        }
    }

    function _renderOrientation(el) {
        // Load static content from orientation.html
        fetch('/content/orientation.html')
            .then(res => res.ok ? res.text() : '')
            .then(html => {
                if (html) {
                    el.appendChild(Sanitize.sanitizeHTML(html));
                } else {
                    Sanitize.setText(el, 'Welcome to the Transmutation Engine.');
                }
            })
            .catch(() => {
                Sanitize.setText(el, 'Welcome to the Transmutation Engine.');
            });
    }

    function _renderAssessment(el) {
        const data = _resultsData.assessment_state;
        if (!data) return;

        const header = document.createElement('h3');
        Sanitize.setText(header, 'Assessment Progress');
        el.appendChild(header);

        // Overall progress
        const overall = document.createElement('div');
        overall.style.margin = '12px 0';
        const answered = data.answered || 0;
        const total = data.total || 200;
        Sanitize.setText(overall, answered + ' / ' + total + ' questions answered');
        el.appendChild(overall);

        const bar = _createProgressBar(answered / total);
        el.appendChild(bar);

        // Per-dimension progress
        if (data.dimension_progress) {
            const dimHeader = document.createElement('h4');
            dimHeader.style.marginTop = '16px';
            Sanitize.setText(dimHeader, 'By Dimension');
            el.appendChild(dimHeader);

            for (const [dim, progress] of Object.entries(data.dimension_progress)) {
                const dimEl = document.createElement('div');
                dimEl.style.margin = '8px 0';
                Sanitize.setText(dimEl, dim + ': ' + progress.answered + '/' + progress.total);
                el.appendChild(dimEl);
                el.appendChild(_createProgressBar(progress.answered / progress.total));
            }
        }
    }

    function _renderProfile(el) {
        const data = _resultsData.profile_snapshots;
        if (!data) return;

        const header = document.createElement('h3');
        Sanitize.setText(header, 'Your Profile');
        el.appendChild(header);

        if (data.quadrant) {
            const quad = document.createElement('div');
            quad.style.margin = '12px 0';
            quad.style.fontSize = '18px';
            quad.style.fontWeight = '600';
            Sanitize.setText(quad, 'Quadrant: ' + data.quadrant);
            el.appendChild(quad);
        }

        if (data.synopsis) {
            const syn = document.createElement('p');
            syn.style.margin = '12px 0';
            syn.style.lineHeight = '1.6';
            Sanitize.setText(syn, data.synopsis);
            el.appendChild(syn);
        }

        if (data.spider_data && data.spider_data.image_base64) {
            const img = document.createElement('img');
            img.src = 'data:image/png;base64,' + data.spider_data.image_base64;
            img.alt = 'Awareness capacity spider chart';
            img.style.maxWidth = '100%';
            img.style.marginTop = '12px';
            el.appendChild(img);
        }

        // Quadrant chart
        const chartContainer = document.createElement('div');
        el.appendChild(chartContainer);
        if (typeof QuadrantChart !== 'undefined') {
            QuadrantChart.render(chartContainer, data.flow_data || null);
        }
    }

    function _renderEducation(el) {
        const data = _resultsData.education_progress;
        if (!data) return;

        const header = document.createElement('h3');
        Sanitize.setText(header, 'Education Progress');
        el.appendChild(header);

        // Summary section
        const summary = data.summary || {};
        if (summary.total_categories) {
            const sumEl = document.createElement('div');
            sumEl.className = 'results-summary';
            sumEl.style.margin = '12px 0';
            const completed = summary.completed_categories || 0;
            const total = summary.total_categories || 0;
            const pct = summary.completion_pct || (total > 0 ? Math.round(completed / total * 100) : 0);
            Sanitize.setText(sumEl, completed + ' / ' + total + ' categories completed (' + pct + '%)');
            el.appendChild(sumEl);
            el.appendChild(_createProgressBar(total > 0 ? completed / total : 0));
        }

        // Per-dimension breakdown
        const progress = data.progress || {};
        const dims = Object.keys(progress);
        if (dims.length > 0) {
            const dimHeader = document.createElement('h4');
            dimHeader.style.marginTop = '16px';
            Sanitize.setText(dimHeader, 'By Dimension');
            el.appendChild(dimHeader);

            for (const dim of dims) {
                const cats = progress[dim] || {};
                const dimEl = document.createElement('div');
                dimEl.className = 'results-dimension';
                dimEl.style.margin = '12px 0';

                const dimLabel = document.createElement('strong');
                Sanitize.setText(dimLabel, dim);
                dimEl.appendChild(dimLabel);

                for (const [catName, catData] of Object.entries(cats)) {
                    const catEl = document.createElement('div');
                    catEl.style.margin = '4px 0 4px 12px';
                    const score = catData.understanding_score || 0;
                    const label = catName.replace(/_/g, ' ');
                    Sanitize.setText(catEl, label + ': ' + score + '%');
                    dimEl.appendChild(catEl);
                    dimEl.appendChild(_createProgressBar(score / 100));
                }

                el.appendChild(dimEl);
            }
        }
    }

    function _renderDevelopment(el) {
        const data = _resultsData.development_roadmap;
        if (!data) return;

        const header = document.createElement('h3');
        Sanitize.setText(header, 'Development');
        el.appendChild(header);

        // Practice count
        const practiceCount = data.practice_count || 0;
        const countEl = document.createElement('div');
        countEl.style.margin = '12px 0';
        Sanitize.setText(countEl, 'Practice entries: ' + practiceCount + ' / 10');
        el.appendChild(countEl);
        el.appendChild(_createProgressBar(Math.min(practiceCount / 10, 1)));

        // Roadmap
        const roadmap = data.roadmap;
        if (roadmap) {
            const rmHeader = document.createElement('h4');
            rmHeader.style.marginTop = '16px';
            Sanitize.setText(rmHeader, 'Current Roadmap');
            el.appendChild(rmHeader);

            if (data.roadmap_created_at) {
                const dateEl = document.createElement('div');
                dateEl.style.color = 'var(--color-text-muted)';
                dateEl.style.fontSize = '13px';
                dateEl.style.marginBottom = '8px';
                Sanitize.setText(dateEl, 'Created: ' + new Date(data.roadmap_created_at).toLocaleDateString());
                el.appendChild(dateEl);
            }

            const steps = roadmap.steps || roadmap;
            if (Array.isArray(steps)) {
                const ol = document.createElement('ol');
                ol.style.paddingLeft = '20px';
                for (const step of steps) {
                    const li = document.createElement('li');
                    li.style.margin = '8px 0';
                    li.style.lineHeight = '1.5';
                    const text = typeof step === 'string' ? step : (step.title || step.description || JSON.stringify(step));
                    Sanitize.setText(li, text);
                    ol.appendChild(li);
                }
                el.appendChild(ol);
            }
        }
    }

    function _renderReassessment(el) {
        const data = _resultsData.comparison_snapshots;
        if (!data) return;

        const header = document.createElement('h3');
        Sanitize.setText(header, 'Reassessment / Check-in');
        el.appendChild(header);

        // If this is check-in data (from API response)
        if (data.count !== undefined) {
            const countEl = document.createElement('div');
            countEl.style.margin = '12px 0';
            Sanitize.setText(countEl, 'Check-ins completed: ' + data.count);
            el.appendChild(countEl);

            if (data.latest_regression !== null && data.latest_regression !== undefined) {
                const regEl = document.createElement('div');
                regEl.style.margin = '8px 0';
                regEl.style.color = data.latest_regression ? 'var(--color-warning)' : 'var(--color-success, #4caf50)';
                Sanitize.setText(regEl, data.latest_regression ? 'Latest: Regression detected' : 'Latest: No regression');
                el.appendChild(regEl);
            }

            if (data.latest_created_at) {
                const dateEl = document.createElement('div');
                dateEl.style.color = 'var(--color-text-muted)';
                dateEl.style.fontSize = '13px';
                Sanitize.setText(dateEl, 'Last check-in: ' + new Date(data.latest_created_at).toLocaleDateString());
                el.appendChild(dateEl);
            }
            return;
        }

        // SSE comparison data (deltas)
        if (data.deltas) {
            const deltaHeader = document.createElement('h4');
            deltaHeader.style.marginTop = '12px';
            Sanitize.setText(deltaHeader, 'Score Changes');
            el.appendChild(deltaHeader);

            for (const [dim, delta] of Object.entries(data.deltas)) {
                const row = document.createElement('div');
                row.style.margin = '6px 0';
                const arrow = delta.direction === 'up' ? '\u2191' : delta.direction === 'down' ? '\u2193' : '\u2194';
                const color = delta.direction === 'up' ? 'var(--color-success, #4caf50)' : delta.direction === 'down' ? 'var(--color-warning)' : 'inherit';
                row.style.color = color;
                Sanitize.setText(row, dim + ': ' + arrow + ' ' + (delta.delta > 0 ? '+' : '') + delta.delta + '%');
                el.appendChild(row);
            }
        }

        if (data.quadrant_shift && data.quadrant_shift.shifted) {
            const shiftEl = document.createElement('div');
            shiftEl.style.margin = '12px 0';
            shiftEl.style.fontWeight = '600';
            Sanitize.setText(shiftEl, 'Quadrant shift: ' + (data.quadrant_shift.from || '?') + ' \u2192 ' + (data.quadrant_shift.to || '?'));
            el.appendChild(shiftEl);
        }
    }

    function _renderGraduation(el) {
        const data = _resultsData.graduation_data;
        if (!data) return;

        const header = document.createElement('h3');
        Sanitize.setText(header, 'Graduation');
        el.appendChild(header);

        if (data.created_at) {
            const dateEl = document.createElement('div');
            dateEl.style.margin = '8px 0';
            dateEl.style.color = 'var(--color-text-muted)';
            dateEl.style.fontSize = '13px';
            Sanitize.setText(dateEl, 'Graduated: ' + new Date(data.created_at).toLocaleDateString());
            el.appendChild(dateEl);
        }

        // Pattern narrative
        if (data.pattern_narrative) {
            const narHeader = document.createElement('h4');
            narHeader.style.marginTop = '12px';
            Sanitize.setText(narHeader, 'Your Pattern Narrative');
            el.appendChild(narHeader);

            const narEl = document.createElement('p');
            narEl.style.margin = '8px 0';
            narEl.style.lineHeight = '1.6';
            Sanitize.setText(narEl, data.pattern_narrative);
            el.appendChild(narEl);
        }

        // Graduation indicators
        const indicators = data.graduation_indicators;
        if (indicators) {
            const indHeader = document.createElement('h4');
            indHeader.style.marginTop = '16px';
            Sanitize.setText(indHeader, 'Convergence Indicators');
            el.appendChild(indHeader);

            for (const [name, ind] of Object.entries(indicators)) {
                const row = document.createElement('div');
                row.style.margin = '6px 0';
                const label = name.replace(/_/g, ' ');
                const status = ind.met ? '\u2713' : '\u2717';
                const color = ind.met ? 'var(--color-success, #4caf50)' : 'var(--color-text-muted)';
                row.style.color = color;
                Sanitize.setText(row, status + ' ' + label + (ind.evidence ? ' — ' + ind.evidence : ''));
                el.appendChild(row);
            }
        }
    }

    function _renderPlaceholder(el, title, description) {
        const h = document.createElement('h3');
        Sanitize.setText(h, title);
        el.appendChild(h);
        const p = document.createElement('p');
        p.style.margin = '12px 0';
        p.style.color = 'var(--color-text-muted)';
        Sanitize.setText(p, description);
        el.appendChild(p);
    }

    function _createProgressBar(fraction) {
        const bar = document.createElement('div');
        bar.className = 'progress-bar';
        const fill = document.createElement('div');
        fill.className = 'progress-bar__fill';
        fill.style.width = Math.min(Math.max(fraction * 100, 0), 100) + '%';
        bar.appendChild(fill);
        return bar;
    }

    return {
        update,
        handlePhaseTransition,
        handleSSEEvent
    };
})();
