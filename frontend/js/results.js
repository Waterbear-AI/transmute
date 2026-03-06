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
     */
    function update(data, currentPhase) {
        _resultsData = data || {};
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
    }

    function _renderEducation(el) {
        const data = _resultsData.education_progress;
        if (!data) return;
        _renderPlaceholder(el, 'Education', 'Education progress and comprehension scores will appear here.');
    }

    function _renderDevelopment(el) {
        const data = _resultsData.development_roadmap;
        if (!data) return;
        _renderPlaceholder(el, 'Development', 'Your development roadmap and practice log will appear here.');
    }

    function _renderReassessment(el) {
        const data = _resultsData.comparison_snapshots;
        if (!data) return;
        _renderPlaceholder(el, 'Reassessment', 'Score comparisons and movement tracking will appear here.');
    }

    function _renderGraduation(el) {
        const data = _resultsData.graduation_data;
        if (!data) return;
        _renderPlaceholder(el, 'Graduation', 'Your journey timeline and graduation summary will appear here.');
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
