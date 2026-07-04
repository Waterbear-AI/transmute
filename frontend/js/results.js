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

    // Canonical 5 education categories per dimension, in teaching order.
    // Mirrors EDUCATION_CATEGORIES in agents/transmutation/tools.py — keep in
    // sync. Every dimension always shows all 5 (untouched ones render at 0%).
    const EDUCATION_CATEGORIES = [
        { key: 'what_this_means', label: 'What This Means' },
        { key: 'your_score', label: 'Your Score' },
        { key: 'daily_effects', label: 'Daily Effects' },
        { key: 'strengths_gaps', label: 'Strengths & Gaps' },
        { key: 'external_interaction', label: 'External Interaction' },
    ];

    let _currentPhase = 'orientation';
    let _activeTab = 'orientation';
    let _resultsData = {};
    let _userId = null;

    /**
     * Update the results panel with data from /api/results/{user_id}.
     * Maps API response keys to internal data keys used by tabs.
     */
    function update(data, currentPhase) {
        const d = data || {};
        if (d.user_id) _userId = d.user_id;
        _resultsData = {};

        // Map API response to internal data keys
        if (d.assessment && d.assessment.exists) _resultsData.assessment_state = d.assessment;
        if (d.latest_profile) _resultsData.profile_snapshots = d.latest_profile;
        if (d.education && d.education.exists) _resultsData.education_progress = d.education;
        // B6.4: assign development data when has_roadmap OR user is in development phase,
        // so the FR-10 "no roadmap" empty state can render for active development users.
        if (d.development && (d.development.has_roadmap || currentPhase === 'development')) {
            _resultsData.development_roadmap = d.development;
        }
        if (d.graduation && d.graduation.exists) _resultsData.graduation_data = d.graduation;
        // Precedence: check-in data first, then reassessment overrides it (ADR-5).
        // Reassessment wins because the backend only sets available=true when the
        // latest snapshot is a reassessment — i.e. the most-recent lifecycle activity.
        if (d.check_ins && d.check_ins.count > 0) _resultsData.comparison_snapshots = d.check_ins;
        if (d.reassessment && d.reassessment.available) _resultsData.comparison_snapshots = d.reassessment;

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
                // SSE data shape: { progress: { answered, total, dimension_progress } }
                // Preserve any early_result already stored (e.g. from a prior
                // assessment.transmute_result event) — this event only refreshes
                // progress-bar fields, and both events can arrive within the same tier.
                _resultsData.assessment_state = Object.assign(
                    {}, data.progress || data,
                    { early_result: (_resultsData.assessment_state && _resultsData.assessment_state.early_result) || null }
                );
                if (_activeTab === 'assessment') _renderTabContent('assessment');
                // Ensure tab is visible
                _renderTabs();
                break;

            case 'assessment.transmute_result':
                // Tier-1 completion fires this once, mid-conversation. Merge into
                // the existing assessment_state (do not clobber answered/total/
                // dimension_progress) so the early-result card and the progress
                // bars can coexist and _renderAssessment can redraw both from
                // stored state alone — required for it to survive tab switches
                // (B6.2) without depending on event-arrival order.
                _resultsData.assessment_state = Object.assign(
                    {}, _resultsData.assessment_state, { early_result: data }
                );
                if (_activeTab === 'assessment') _renderTabContent('assessment');
                _renderTabs();
                break;

            case 'profile.snapshot':
                _resultsData.profile_snapshots = data;
                // Render tabs first so the Profile tab becomes visible (it is
                // visibility-gated on profile_snapshots being non-null), then
                // switch to it. Switching before _renderTabs() would select a
                // hidden tab and render into an invisible container.
                _renderTabs();
                _switchTab('profile');
                // Re-fetch full results to get spider chart (binary data not sent via SSE)
                // and to populate comparison_snapshots from results.reassessment when available (FR-4).
                if (_userId) {
                    fetch('/api/results/' + encodeURIComponent(_userId))
                        .then(r => r.ok ? r.json() : null)
                        .then(results => {
                            if (!results) return;
                            if (results.latest_profile) {
                                _resultsData.profile_snapshots = results.latest_profile;
                                if (_activeTab === 'profile') _renderTabContent('profile');
                            }
                            // FR-4: populate reassessment comparison data post-persistence.
                            // The re-fetch runs after save_profile_snapshot persists the snapshot,
                            // so results.reassessment now reflects the current reassessment correctly.
                            if (results.reassessment && results.reassessment.available) {
                                _resultsData.comparison_snapshots = results.reassessment;
                                _renderTabs();
                                if (_activeTab === 'reassessment') _renderTabContent('reassessment');
                            }
                        })
                        .catch(() => {});
                }
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
                // ADR-4: the tool payload shape (total_entries, saved, …) does NOT match the
                // API shape (practices, gate, recent_entries …). Stuffing the raw payload
                // silently zeroes every new field. Refetch the full results instead —
                // same pattern as profile.snapshot (results.js:91-102).
                _renderTabs();
                if (_userId) {
                    fetch('/api/results/' + encodeURIComponent(_userId))
                        .then(function(r) { return r.ok ? r.json() : null; })
                        .then(function(results) {
                            if (results && results.development &&
                                (results.development.has_roadmap || _currentPhase === 'development')) {
                                _resultsData.development_roadmap = results.development;
                                if (_activeTab === 'development') _renderTabContent('development');
                                _renderTabs();
                            }
                        })
                        .catch(function() {}); // keep last-known-good data on failure
                }
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
                // Re-fetch full results to get rich regression detail (not in SSE payload)
                if (_userId) {
                    fetch('/api/results/' + encodeURIComponent(_userId))
                        .then(r => r.ok ? r.json() : null)
                        .then(results => {
                            if (results && results.check_ins && results.check_ins.count > 0) {
                                _resultsData.comparison_snapshots = results.check_ins;
                                if (_activeTab === 'reassessment') _renderTabContent('reassessment');
                                _renderTabs();
                            }
                        })
                        .catch(() => {});
                }
                break;
        }
    }

    function _renderTabs() {
        const tabsEl = document.getElementById('results-tabs');
        tabsEl.textContent = '';

        // Phase stepper above tabs
        const headerEl = document.getElementById('results-header');
        let stepperContainer = headerEl.querySelector('.phase-stepper-container');
        if (!stepperContainer) {
            stepperContainer = document.createElement('div');
            stepperContainer.className = 'phase-stepper-container';
            headerEl.insertBefore(stepperContainer, tabsEl);
        }
        if (typeof PhaseStepper !== 'undefined') {
            PhaseStepper.render(stepperContainer, _currentPhase, (phaseId) => {
                _switchTab(phaseId);
            });
        }

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
        // Assessment: visible when in assessment phase or has data
        if (tab.id === 'assessment') {
            return _currentPhase === 'assessment' || !!_resultsData.assessment_state;
        }
        // Reassessment: visible during the reassessment phase (even before any snapshot
        // exists, so the empty state is reachable) OR when comparison data is present
        // (post-graduation check-ins also land in comparison_snapshots — ADR-6, FR-10).
        if (tab.id === 'reassessment') {
            return _currentPhase === 'reassessment' || !!_resultsData.comparison_snapshots;
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

    function _showLoading(el) {
        const loader = document.createElement('div');
        loader.className = 'results-loading';
        const spinner = document.createElement('div');
        spinner.className = 'spinner';
        loader.appendChild(spinner);
        const text = document.createElement('span');
        Sanitize.setText(text, 'Loading...');
        loader.appendChild(text);
        el.appendChild(loader);
    }

    function _renderOrientation(el) {
        _showLoading(el);
        fetch('/content/orientation.html')
            .then(res => res.ok ? res.text() : '')
            .then(html => {
                el.textContent = '';
                if (html) {
                    el.appendChild(Sanitize.sanitizeHTML(html));
                } else {
                    Sanitize.setText(el, 'Welcome to the Transmutation Engine.');
                }
            })
            .catch(() => {
                el.textContent = '';
                Sanitize.setText(el, 'Welcome to the Transmutation Engine.');
            });
    }

    function _renderAssessment(el) {
        const data = _resultsData.assessment_state;

        const header = document.createElement('h3');
        header.id = 'assessment-progress-header';
        Sanitize.setText(header, 'Assessment Progress');
        el.appendChild(header);

        // Tier affordance — always server-authoritative (assessment_tier column);
        // this component only displays it, never computes/advances it client-side.
        if (typeof TierProgress !== 'undefined') {
            el.appendChild(TierProgress.create(data && data.assessment_tier));
        }

        // Early transmute result — rendered from stored state (not directly from
        // the event) so it survives tab switches and page reloads alike (B6.2):
        // the same data.early_result key is populated by the assessment.transmute_result
        // SSE handler above AND by a GET /api/results fetch on initial load.
        if (data && data.early_result && typeof renderEarlyResult === 'function') {
            renderEarlyResult(el, data.early_result);
        }

        const answered = (data && data.answered) || 0;
        const total = (data && data.total) || 200;

        // Overall progress
        const overall = document.createElement('div');
        overall.id = 'assessment-progress-overall';
        overall.style.margin = '12px 0';
        Sanitize.setText(overall, answered + ' / ' + total + ' questions answered');
        el.appendChild(overall);

        const bar = _createProgressBar(total > 0 ? answered / total : 0);
        bar.id = 'assessment-progress-bar';
        el.appendChild(bar);

        // Percentage
        const pct = document.createElement('div');
        pct.style.color = 'var(--color-text-muted)';
        pct.style.fontSize = '13px';
        pct.style.marginTop = '4px';
        Sanitize.setText(pct, Math.round((answered / Math.max(total, 1)) * 100) + '% complete');
        el.appendChild(pct);

        // Per-dimension progress
        if (data && data.dimension_progress) {
            const dimHeader = document.createElement('h4');
            dimHeader.style.marginTop = '16px';
            Sanitize.setText(dimHeader, 'By Dimension');
            el.appendChild(dimHeader);

            for (const [dim, progress] of Object.entries(data.dimension_progress)) {
                const dimRow = document.createElement('div');
                dimRow.style.margin = '8px 0';

                const dimLabel = document.createElement('div');
                dimLabel.style.display = 'flex';
                dimLabel.style.justifyContent = 'space-between';
                dimLabel.style.marginBottom = '2px';

                const nameEl = document.createElement('span');
                Sanitize.setText(nameEl, dim);
                dimLabel.appendChild(nameEl);

                const countEl = document.createElement('span');
                countEl.style.color = 'var(--color-text-muted)';
                countEl.style.fontSize = '13px';
                Sanitize.setText(countEl, progress.answered + '/' + progress.total);
                dimLabel.appendChild(countEl);

                dimRow.appendChild(dimLabel);

                const dimBar = _createProgressBar(progress.total > 0 ? progress.answered / progress.total : 0);
                dimRow.appendChild(dimBar);

                el.appendChild(dimRow);
            }
        } else if (!data) {
            const hint = document.createElement('p');
            hint.style.color = 'var(--color-text-muted)';
            hint.style.marginTop = '12px';
            Sanitize.setText(hint, 'Answer questions in the chat to see your progress here.');
            el.appendChild(hint);
        }
    }

    /**
     * Build a short description for an early (Tier-1-only) transmute result.
     * Deliberately does NOT reuse _buildTransmuteDescription's sub-dimension
     * sentence — Tier 1 has no per-dimension scores yet (only x/y/archetype),
     * so that sentence would either be wrong or require threading a "have
     * scores" flag through a function whose only other caller (the real
     * Profile tab) always has them. A small dedicated description avoids
     * that risk entirely (spec.md B6.1 explicitly allows this alternative).
     */
    function _buildEarlyResultDescription(shim) {
        const name = _archetypeName(shim);
        const key = _archetypeKey(shim);
        const meaning = (key && ARCHETYPE_DESCRIPTIONS[key]) || DEFAULT_ARCHETYPE_DESC;
        if (name) {
            return 'Early read: your pattern is leaning ' + name + ' — ' + meaning;
        }
        return 'Early read: ' + meaning.charAt(0).toUpperCase() + meaning.slice(1);
    }

    /**
     * Render the Tier-1 early transmute result card from a stored
     * assessment.transmute_result payload: { archetype, x, y, confidence,
     * confidence_reason }. Called both from the SSE handler (live) and from
     * _renderAssessment reading _resultsData.assessment_state.early_result
     * (post tab-switch / page-reload) — the two call sites always pass the
     * identical raw event/API shape, so there is exactly one code path that
     * interprets it.
     *
     * Builds a "shim" ({quadrant_placement: {archetype, x, y, confidence}})
     * matching the real profile snapshot's shape so QuadrantChart.render and
     * the _archetypeName/_archetypeKey helpers (written for full snapshots)
     * work unmodified against this partial Tier-1 payload too — see
     * spec.md B6.1.
     */
    function renderEarlyResult(el, earlyResult) {
        if (!earlyResult) return;

        const shim = {
            quadrant_placement: {
                archetype: earlyResult.archetype,
                x: earlyResult.x,
                y: earlyResult.y,
                confidence: earlyResult.confidence
            }
        };

        const card = document.createElement('div');
        card.className = 'early-result-card';
        card.id = 'early-transmute-result';
        // Announce the archetype + description to screen readers when the card
        // first appears (mid-conversation, SSE-driven) — matches the
        // role="status" + aria-live="polite" pattern used by the regression
        // panel elsewhere in this file. The nested ConfidenceBand has its own
        // role="status" for the confidence badge specifically; this outer
        // region covers the archetype title + description text too.
        card.setAttribute('role', 'status');
        card.setAttribute('aria-live', 'polite');

        const title = document.createElement('h4');
        Sanitize.setText(title, 'Your Early Transmute Read');
        card.appendChild(title);

        if (typeof ConfidenceBand !== 'undefined') {
            card.appendChild(ConfidenceBand.create(earlyResult.confidence, earlyResult.confidence_reason));
        }

        const desc = document.createElement('p');
        desc.className = 'early-result-card__desc';
        Sanitize.setText(desc, _buildEarlyResultDescription(shim));
        card.appendChild(desc);

        const chartContainer = document.createElement('div');
        card.appendChild(chartContainer);
        if (typeof QuadrantChart !== 'undefined') {
            QuadrantChart.render(chartContainer, shim.quadrant_placement, null);
        }

        el.appendChild(card);
    }

    function _renderProfile(el) {
        const data = _resultsData.profile_snapshots;
        if (!data) return;

        const header = document.createElement('h3');
        Sanitize.setText(header, 'Your Profile');
        el.appendChild(header);

        // Archetype name — snapshots store it at quadrant_placement.archetype
        // (lowercase, e.g. "magnifier"); older payloads used data.quadrant or
        // quadrant_placement.quadrant. _archetypeName normalizes all of these.
        const quadrantName = _archetypeName(data);

        if (quadrantName) {
            const quad = document.createElement('div');
            quad.style.margin = '12px 0';
            quad.style.fontSize = '18px';
            quad.style.fontWeight = '600';
            Sanitize.setText(quad, 'Quadrant: ' + quadrantName);
            el.appendChild(quad);
        }

        // Interpretation text — from SSE (data.interpretation) or API (data.interpretation)
        const interpretation = data.interpretation || data.synopsis;
        if (interpretation) {
            const syn = document.createElement('p');
            syn.style.margin = '12px 0';
            syn.style.lineHeight = '1.6';
            Sanitize.setText(syn, interpretation);
            el.appendChild(syn);
        }

        // Transmute graph — collapsible: result-specific description + quadrant chart
        const transmuteBody = _renderCollapsibleSection(el, 'Your Transmute Pattern', false);
        const tDesc = document.createElement('p');
        tDesc.className = 'profile-collapsible__desc';
        Sanitize.setText(tDesc, _buildTransmuteDescription(data));
        transmuteBody.appendChild(tDesc);
        const chartContainer = document.createElement('div');
        transmuteBody.appendChild(chartContainer);
        if (typeof QuadrantChart !== 'undefined') {
            QuadrantChart.render(chartContainer, data.quadrant_placement || null, data.flow_data || null);
        }

        // Awareness & Transmute Capacity Profile — collapsible: description + spider chart
        const spiderBody = _renderCollapsibleSection(el, 'Awareness & Transmute Capacity Profile', false);
        const sDesc = document.createElement('p');
        sDesc.className = 'profile-collapsible__desc';
        Sanitize.setText(sDesc, _buildSpiderDescription(data));
        spiderBody.appendChild(sDesc);
        if (data.spider_data && data.spider_data.image_base64) {
            const img = document.createElement('img');
            img.src = 'data:image/png;base64,' + data.spider_data.image_base64;
            img.alt = 'Awareness capacity spider chart';
            img.style.maxWidth = '100%';
            img.style.marginTop = '12px';
            spiderBody.appendChild(img);
        }

        // Structured insight sections (strengths, growth areas, cross-dimensional)
        // Only rendered when structured_insights is present — old snapshots fall back
        // to the interpretation paragraph rendered above (no crash, no blank).
        const si = data.structured_insights;
        if (si && typeof si === 'object') {
            // Top Strengths
            if (Array.isArray(si.strengths) && si.strengths.length > 0) {
                _renderInsightSection(el, '🌟 Top Strengths', si.strengths, (item) => {
                    const li = document.createElement('li');
                    li.className = 'profile-insight__item';
                    const summary = document.createElement('div');
                    summary.className = 'profile-insight__summary';
                    const nameSpan = document.createElement('span');
                    Sanitize.setText(nameSpan, item.dimension + ' — ' + item.level);
                    const scoreSpan = document.createElement('span');
                    scoreSpan.className = 'profile-insight__score';
                    Sanitize.setText(scoreSpan, '(' + (Math.round(item.score * 100) / 100) + ')');
                    summary.appendChild(nameSpan);
                    summary.appendChild(scoreSpan);
                    li.appendChild(summary);
                    if (item.note) {
                        const note = document.createElement('div');
                        note.className = 'profile-insight__note';
                        Sanitize.setText(note, item.note);
                        li.appendChild(note);
                    }
                    // Sub-dimension bars for this dimension from scores
                    if (data.scores && data.scores[item.dimension]) {
                        _renderSubDimensionBars(li, data.scores[item.dimension]);
                    }
                    return li;
                });
            }

            // Growth Areas
            if (Array.isArray(si.growth_areas) && si.growth_areas.length > 0) {
                _renderInsightSection(el, '🌱 Growth Areas', si.growth_areas, (item) => {
                    const li = document.createElement('li');
                    li.className = 'profile-insight__item';
                    const summary = document.createElement('div');
                    summary.className = 'profile-insight__summary';
                    const nameSpan = document.createElement('span');
                    Sanitize.setText(nameSpan, item.dimension + ' — ' + item.level);
                    const scoreSpan = document.createElement('span');
                    scoreSpan.className = 'profile-insight__score';
                    Sanitize.setText(scoreSpan, '(' + (Math.round(item.score * 100) / 100) + ')');
                    summary.appendChild(nameSpan);
                    summary.appendChild(scoreSpan);
                    li.appendChild(summary);
                    if (item.note) {
                        const note = document.createElement('div');
                        note.className = 'profile-insight__note';
                        Sanitize.setText(note, item.note);
                        li.appendChild(note);
                    }
                    if (data.scores && data.scores[item.dimension]) {
                        _renderSubDimensionBars(li, data.scores[item.dimension]);
                    }
                    return li;
                });
            }

            // Cross-Dimensional Insights
            if (Array.isArray(si.cross_dimensional_insights) && si.cross_dimensional_insights.length > 0) {
                _renderInsightSection(el, '🕸️ Cross-Dimensional Insights', si.cross_dimensional_insights, (text, idx) => {
                    const li = document.createElement('li');
                    li.className = 'profile-insight__item profile-insight__item--cross';
                    const num = document.createElement('span');
                    num.className = 'profile-insight__num';
                    Sanitize.setText(num, (idx + 1) + '. ');
                    li.appendChild(num);
                    // Sanitize.textNode returns a safe text node — never innerHTML
                    li.appendChild(Sanitize.textNode(String(text)));
                    return li;
                });
            }
        }

        // Dimension scores breakdown — collapsible
        if (data.scores) {
            const scoresBody = _renderCollapsibleSection(el, 'Dimension Scores', false);

            for (const [dim, dimData] of Object.entries(data.scores)) {
                const row = document.createElement('div');
                row.className = 'profile-dim__row';

                const label = document.createElement('div');
                label.className = 'profile-dim__label';

                const nameEl = document.createElement('span');
                Sanitize.setText(nameEl, dim);
                label.appendChild(nameEl);

                const score = typeof dimData === 'object' ? (dimData.weighted_avg || dimData.score || 0) : dimData;
                const scoreEl = document.createElement('span');
                scoreEl.className = 'profile-dim__score';
                Sanitize.setText(scoreEl, Math.round(score * 100) / 100 + '');
                label.appendChild(scoreEl);

                row.appendChild(label);
                const bar = _createProgressBar(Math.min(score / 5, 1));
                bar.setAttribute('aria-label', dim + ': ' + (Math.round(score * 100) / 100));
                row.appendChild(bar);

                // Sub-dimension rows nested under each dimension
                if (typeof dimData === 'object' && dimData.sub_dimensions) {
                    _renderSubDimensionBars(row, dimData);
                }

                scoresBody.appendChild(row);
            }
        }

    }

    // Meaning of each transmute archetype, written as a continuation so it can
    // follow "Your pattern is <Name> — ". No-shame framing: a current operating
    // mode, not a verdict. Keyed by the names QuadrantChart._getArchetype uses.
    const ARCHETYPE_DESCRIPTIONS = {
        Transmuter: 'you tend to filter deprivation AND amplify fulfillment — breaking difficult cycles and spreading the good. This is the pattern most development work aims toward.',
        Absorber: 'you tend to filter deprivation but keep fulfillment private — taking on others’ pain to protect them, while holding your own joy close. The growth edge is letting more of your fulfillment flow outward.',
        Magnifier: 'you amplify what you receive — both the good and the difficult. You’re a person of real presence who moves things; when you’re around good energy you spread it, and hard energy can move through you too. The work ahead is building your filtering capacity so you can choose what you amplify.',
        Extractor: 'you currently tend to amplify deprivation while keeping fulfillment for yourself. These patterns usually develop for good reasons — survival, protection. The growth edge is filtering what you pass on and sharing more of the good.',
        Conduit: 'you mostly pass through what you receive without significantly transforming it — the morally-neutral baseline where most people operate most of the time. It’s a solid place to build deliberate filtering and amplification from.'
    };
    const DEFAULT_ARCHETYPE_DESC = 'your transmute pattern describes how you tend to handle deprivation and fulfillment — what you filter, what you pass through, and what you amplify outward.';
    const SPIDER_DESCRIPTION = 'This radar maps your awareness capacity across every dimension. Points further from the center are areas of strength; points closer in are opportunities to grow.';

    /** Round to 2 decimals for display. */
    function _round2(n) { return Math.round((Number(n) || 0) * 100) / 100; }

    // The five transmute archetypes (matches QuadrantChart._getArchetype).
    const ARCHETYPE_KEYS = ['Transmuter', 'Absorber', 'Magnifier', 'Extractor', 'Conduit'];

    /** Raw archetype string from whichever field the snapshot carries. */
    function _archetypeRaw(data) {
        const qp = (data && data.quadrant_placement) || {};
        return (typeof data.quadrant === 'string' && data.quadrant)
            || qp.quadrant || qp.archetype || null;
    }

    /**
     * Human-facing archetype label for display. Keeps an already-formatted
     * string ("The Magnifier") as-is; capitalizes a bare lowercase word
     * ("magnifier" -> "Magnifier", as stored in quadrant_placement.archetype).
     */
    function _archetypeName(data) {
        const raw = _archetypeRaw(data);
        if (!raw) return null;
        const s = String(raw).trim();
        return /^[a-z]+$/.test(s) ? s.charAt(0).toUpperCase() + s.slice(1) : s;
    }

    /**
     * Normalized archetype KEY for ARCHETYPE_DESCRIPTIONS lookup. Finds the
     * known archetype word inside the raw string ("The Magnifier" -> "Magnifier",
     * "magnifier (medium)" -> "Magnifier"). Returns null if none match.
     */
    function _archetypeKey(data) {
        const raw = _archetypeRaw(data);
        if (!raw) return null;
        const lower = String(raw).toLowerCase();
        for (let i = 0; i < ARCHETYPE_KEYS.length; i++) {
            if (lower.indexOf(ARCHETYPE_KEYS[i].toLowerCase()) !== -1) return ARCHETYPE_KEYS[i];
        }
        return null;
    }

    /** Extract sorted [name, score] pairs from a dimension's sub_dimensions. */
    function _subDimPairs(dimEntry) {
        const subs = dimEntry && dimEntry.sub_dimensions;
        if (!subs || typeof subs !== 'object') return [];
        return Object.entries(subs)
            .map(([k, v]) => [k, typeof v === 'object' ? (v.score || 0) : (v || 0)])
            .filter(function (p) { return p[1] > 0; })
            .sort(function (a, b) { return b[1] - a[1]; });
    }

    /**
     * Build a description of the user's ACTUAL transmute result: their archetype
     * + confidence, what it means, and the specific Transmutation Capacity
     * sub-scores that drove the placement.
     */
    function _buildTransmuteDescription(data) {
        const name = _archetypeName(data);
        const key = _archetypeKey(data);
        const qp = (data && data.quadrant_placement) || {};
        const meaning = (key && ARCHETYPE_DESCRIPTIONS[key]) || DEFAULT_ARCHETYPE_DESC;
        let text;
        if (name) {
            const conf = qp.confidence ? ' (' + qp.confidence + ' confidence)' : '';
            text = 'Your pattern is ' + name + conf + ' — ' + meaning;
        } else {
            text = meaning.charAt(0).toUpperCase() + meaning.slice(1);
        }
        const pairs = _subDimPairs(data.scores && data.scores['Transmutation Capacity']);
        if (pairs.length >= 2) {
            const hi = pairs[0];
            const lo = pairs[pairs.length - 1];
            text += ' This shows in your results: ' + hi[0] + ' is your strongest transmute capacity ('
                + _round2(hi[1]) + '/5), while ' + lo[0] + ' has the most room to grow ('
                + _round2(lo[1]) + '/5).';
        }
        return text;
    }

    /**
     * Build a description of the user's ACTUAL spider-chart result: which
     * awareness dimensions are highest and lowest.
     */
    function _buildSpiderDescription(data) {
        const scores = data && data.scores;
        if (!scores || typeof scores !== 'object') return SPIDER_DESCRIPTION;
        const dims = Object.entries(scores)
            .map(([k, v]) => [k, typeof v === 'object' ? (v.score || 0) : (v || 0)])
            .filter(function (p) { return p[1] > 0; })
            .sort(function (a, b) { return b[1] - a[1]; });
        if (dims.length < 2) return SPIDER_DESCRIPTION;
        const fmt = function (arr) {
            return arr.map(function (p) { return p[0] + ' (' + _round2(p[1]) + ')'; }).join(', ');
        };
        const top = dims.slice(0, 3);
        const bottom = dims.slice(-3).reverse();
        return 'Your awareness is strongest in ' + fmt(top)
            + ', and has the most room to grow in ' + fmt(bottom)
            + '. On the radar, points further from the center are your strengths; points closer in are growth areas.';
    }

    let _collapsibleSeq = 0;

    /**
     * Render a collapsible section: a heading with a real <button> toggle
     * (keyboard-operable, aria-expanded) and a body the caller fills. Mirrors
     * the Likert batch collapse UX for consistency. Returns the body element.
     * @param {Element} parentEl - Container to append the section into.
     * @param {string} title - Section title (may include a decorative emoji).
     * @param {boolean} startCollapsed - If true, render initially collapsed.
     * @param {string} [extraClass] - Optional extra class on the section element
     *   (e.g. 'profile-insight-section' so existing selectors/tests still match).
     * @returns {Element} the body element to append content into.
     */
    function _renderCollapsibleSection(parentEl, title, startCollapsed, extraClass) {
        const section = document.createElement('section');
        section.className = 'profile-collapsible';
        if (extraClass) section.classList.add(extraClass);
        if (startCollapsed) section.classList.add('profile-collapsible--collapsed');

        // Heading wraps a button (WAI-ARIA disclosure pattern): keep heading
        // semantics on <h4>, put the interactive role on the nested <button>.
        const heading = document.createElement('h4');
        heading.className = 'profile-collapsible__heading';

        const toggle = document.createElement('button');
        toggle.type = 'button';
        toggle.className = 'profile-collapsible__toggle';
        toggle.setAttribute('aria-expanded', startCollapsed ? 'false' : 'true');

        const chevron = document.createElement('span');
        chevron.className = 'profile-collapsible__chevron';
        chevron.setAttribute('aria-hidden', 'true');
        chevron.textContent = startCollapsed ? '▶' : '▼';  // ▶ collapsed / ▼ expanded
        toggle.appendChild(chevron);

        const titleSpan = document.createElement('span');
        titleSpan.className = 'profile-collapsible__title';
        // Strip decorative emoji from the accessible name.
        toggle.setAttribute('aria-label', title.replace(/[\u{1F000}-\u{1FFFF}]|[☀-⛿]|[✀-➿]|️/gu, '').trim());
        Sanitize.setText(titleSpan, title);
        toggle.appendChild(titleSpan);

        heading.appendChild(toggle);
        section.appendChild(heading);

        const body = document.createElement('div');
        body.className = 'profile-collapsible__body';
        const bodyId = 'profile-collapsible-' + (++_collapsibleSeq);
        body.id = bodyId;
        toggle.setAttribute('aria-controls', bodyId);
        section.appendChild(body);

        toggle.addEventListener('click', () => {
            const collapsed = section.classList.toggle('profile-collapsible--collapsed');
            toggle.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
            chevron.textContent = collapsed ? '▶' : '▼';
        });

        parentEl.appendChild(section);
        return body;
    }

    /**
     * Render a titled, collapsible insight section (Strengths, Growth Areas,
     * Cross-Dimensional Insights).
     * @param {Element} parentEl - Container to append the section into.
     * @param {string} title - Section title text (may include emoji).
     * @param {Array} items - Array of items to render.
     * @param {Function} renderItemFn - (item, index) => HTMLElement for each item.
     */
    function _renderInsightSection(parentEl, title, items, renderItemFn) {
        // Keep the 'profile-insight-section' class so existing selectors/e2e
        // locators (which filter by it) continue to match the section.
        const body = _renderCollapsibleSection(parentEl, title, false, 'profile-insight-section');

        const list = document.createElement('ul');
        list.className = 'profile-insight-section__list';

        items.forEach((item, idx) => {
            const el = renderItemFn(item, idx);
            if (el) list.appendChild(el);
        });

        body.appendChild(list);
    }

    /**
     * Render sub-dimension progress bars indented under a parent dimension row.
     * Reads sub_dimensions from dimData ({sd: {score, answered}}).
     * @param {Element} parentEl - Row element to append bars into.
     * @param {Object} dimData - Dimension data object with sub_dimensions map.
     */
    function _renderSubDimensionBars(parentEl, dimData) {
        const subDims = dimData && dimData.sub_dimensions;
        if (!subDims || typeof subDims !== 'object') return;

        for (const [sdName, sdData] of Object.entries(subDims)) {
            const sdScore = typeof sdData === 'object' ? (sdData.score || 0) : sdData;
            const sdRow = document.createElement('div');
            sdRow.className = 'profile-subdim__row';

            const sdLabel = document.createElement('div');
            sdLabel.className = 'profile-subdim__label';
            const arrow = document.createElement('span');
            arrow.className = 'profile-subdim__arrow';
            arrow.setAttribute('aria-hidden', 'true');
            Sanitize.setText(arrow, '▸ ');
            const sdNameEl = document.createElement('span');
            Sanitize.setText(sdNameEl, sdName);
            sdLabel.appendChild(arrow);
            sdLabel.appendChild(sdNameEl);

            const sdScoreEl = document.createElement('span');
            sdScoreEl.className = 'profile-subdim__score';
            Sanitize.setText(sdScoreEl, Math.round(sdScore * 100) / 100 + '');
            sdLabel.appendChild(sdScoreEl);

            sdRow.appendChild(sdLabel);

            const sdBar = _createProgressBar(Math.min(sdScore / 5, 1));
            sdBar.className += ' profile-subdim__bar';
            sdBar.setAttribute('aria-label', sdName + ': ' + (Math.round(sdScore * 100) / 100));
            sdRow.appendChild(sdBar);

            parentEl.appendChild(sdRow);
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

                // Always render all 5 canonical categories in teaching order,
                // even ones the user hasn't reached yet (they show 0%), so the
                // tab reflects the full dimension — e.g. "Category 5: External
                // Interaction" is visible before it's started.
                EDUCATION_CATEGORIES.forEach((cat, i) => {
                    const catData = cats[cat.key] || {};
                    const catEl = document.createElement('div');
                    catEl.style.margin = '4px 0 4px 12px';
                    const score = catData.understanding_score || 0;
                    Sanitize.setText(catEl, (i + 1) + '. ' + cat.label + ': ' + score + '%');
                    dimEl.appendChild(catEl);
                    dimEl.appendChild(_createProgressBar(score / 100));
                });

                el.appendChild(dimEl);
            }
        }
    }

    // ── Development tab helpers ──────────────────────────────────────────────

    /**
     * Render the dual-path gate progress block (FR-6) with optional ready banner (FR-7).
     */
    function _renderDevGate(el, data) {
        const gate = data.gate || null;
        const entriesLogged = (gate && gate.entries_logged != null) ? gate.entries_logged : (data.practice_count || 0);
        const entriesRequired = (gate && gate.entries_required) || 10;
        const daysElapsed = gate ? gate.days_elapsed : null;
        const daysRequired = (gate && gate.days_required) || 30;

        const section = document.createElement('div');
        section.className = 'dev-gate-block';

        const gateHeader = document.createElement('h4');
        gateHeader.style.marginBottom = '8px';
        Sanitize.setText(gateHeader, 'Progress to reassessment');
        section.appendChild(gateHeader);

        // Entries bar
        const entriesLabel = document.createElement('div');
        entriesLabel.style.marginBottom = '4px';
        Sanitize.setText(entriesLabel, 'Practice entries: ' + entriesLogged + ' / ' + entriesRequired);
        section.appendChild(entriesLabel);
        section.appendChild(_createProgressBar(Math.min(entriesLogged / entriesRequired, 1)));

        // Days bar (only when a roadmap / days_elapsed is available)
        if (daysElapsed !== null && daysElapsed !== undefined) {
            const daysLabel = document.createElement('div');
            daysLabel.style.marginTop = '8px';
            daysLabel.style.marginBottom = '4px';
            Sanitize.setText(daysLabel, 'or ' + daysElapsed + ' / ' + daysRequired + ' days elapsed');
            section.appendChild(daysLabel);
            section.appendChild(_createProgressBar(Math.min(daysElapsed / daysRequired, 1)));
        }

        // Ready-for-reassessment banner (FR-7): only when gate passed AND still in development phase
        if (gate && gate.passed && _currentPhase === 'development') {
            const banner = document.createElement('div');
            banner.className = 'ready-banner';
            banner.setAttribute('role', 'status');
            const bannerText = document.createElement('p');
            Sanitize.setText(bannerText, '✓ Ready for reassessment — say “I’m ready” in chat to begin.');
            banner.appendChild(bannerText);
            section.appendChild(banner);
        }

        el.appendChild(section);
    }

    /**
     * Render structured practice cards (FR-4).
     * Falls back to legacy roadmap.steps ordered list when practices is empty (spec B6.4).
     */
    function _renderDevPractices(el, data) {
        const practices = data.practices || [];
        const roadmap = data.roadmap || null;

        const section = document.createElement('div');
        section.className = 'dev-practices-section';

        // Roadmap header row with creation date
        const rmRow = document.createElement('div');
        rmRow.style.display = 'flex';
        rmRow.style.justifyContent = 'space-between';
        rmRow.style.alignItems = 'baseline';
        rmRow.style.marginBottom = '8px';

        const rmHeader = document.createElement('h4');
        rmHeader.style.margin = '0';
        Sanitize.setText(rmHeader, 'Current Roadmap');
        rmRow.appendChild(rmHeader);

        if (data.roadmap_created_at) {
            const dateEl = document.createElement('div');
            dateEl.style.color = 'var(--color-text-muted)';
            dateEl.style.fontSize = '13px';
            Sanitize.setText(dateEl, 'Created: ' + new Date(data.roadmap_created_at).toLocaleDateString());
            rmRow.appendChild(dateEl);
        }
        section.appendChild(rmRow);

        if (practices.length > 0) {
            // Structured practice cards (new path)
            for (var i = 0; i < practices.length; i++) {
                var p = practices[i];
                var card = document.createElement('div');
                card.className = 'practice-card';

                var titleEl = document.createElement('h5');
                Sanitize.setText(titleEl, p.title || p.practice_id);
                card.appendChild(titleEl);

                var metaEl = document.createElement('div');
                metaEl.className = 'practice-card__meta';
                var metaParts = [p.dimension];
                if (p.transmutation_operation) metaParts.push(p.transmutation_operation);
                Sanitize.setText(metaEl, metaParts.join(' · '));
                card.appendChild(metaEl);

                var statsEl = document.createElement('div');
                statsEl.className = 'practice-card__stats';
                var statParts = [p.entry_count + ' ' + (p.entry_count === 1 ? 'entry' : 'entries')];
                if (p.last_self_rating != null) statParts.push('last rating ' + p.last_self_rating + '/10');
                if (p.last_entry_at) {
                    statParts.push(new Date(p.last_entry_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric' }));
                }
                Sanitize.setText(statsEl, statParts.join(' · '));
                card.appendChild(statsEl);

                section.appendChild(card);
            }
        } else if (roadmap) {
            // Legacy fallback: render roadmap.steps ordered list when no structured practices
            var steps = roadmap.steps || roadmap;
            if (Array.isArray(steps)) {
                var ol = document.createElement('ol');
                ol.style.paddingLeft = '20px';
                for (var j = 0; j < steps.length; j++) {
                    var step = steps[j];
                    var li = document.createElement('li');
                    li.style.margin = '8px 0';
                    li.style.lineHeight = '1.5';
                    var text = typeof step === 'string' ? step : (step.title || step.description || JSON.stringify(step));
                    Sanitize.setText(li, text);
                    ol.appendChild(li);
                }
                section.appendChild(ol);
            }
        }

        el.appendChild(section);
    }

    /**
     * Render the recent journal entries list (FR-5).
     * reflection is user-authored — all text via Sanitize.setText (never innerHTML).
     */
    function _renderDevJournal(el, data) {
        var entries = data.recent_entries || [];
        var totalEntries = data.total_entries != null ? data.total_entries : (data.practice_count || 0);

        var section = document.createElement('div');
        section.className = 'dev-journal-section';

        var jHeader = document.createElement('h4');
        var headerText = 'Recent journal entries';
        if (entries.length > 0) {
            headerText += ' (' + entries.length + ' of ' + totalEntries + ')';
        }
        Sanitize.setText(jHeader, headerText);
        section.appendChild(jHeader);

        if (entries.length === 0) {
            var emptyEl = document.createElement('p');
            emptyEl.style.color = 'var(--color-text-muted)';
            emptyEl.style.marginTop = '8px';
            Sanitize.setText(emptyEl, 'No journal entries yet — tell me in chat how a practice went to log your first.');
            section.appendChild(emptyEl);
        } else {
            var ul = document.createElement('ul');
            ul.className = 'journal-list';
            ul.setAttribute('aria-label', 'Recent journal entries');

            for (var k = 0; k < entries.length; k++) {
                var entry = entries[k];
                var li = document.createElement('li');
                li.className = 'journal-entry';

                var entryMeta = document.createElement('div');
                entryMeta.className = 'journal-entry__meta';
                var metaParts = [];
                if (entry.created_at) {
                    metaParts.push(new Date(entry.created_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric' }));
                }
                if (entry.self_rating != null) metaParts.push(entry.self_rating + '/10');
                if (entry.dimension) metaParts.push(entry.dimension);
                Sanitize.setText(entryMeta, metaParts.join(' · '));
                li.appendChild(entryMeta);

                var entryText = document.createElement('p');
                entryText.className = 'journal-entry__reflection';
                Sanitize.setText(entryText, entry.reflection || '');
                li.appendChild(entryText);

                ul.appendChild(li);
            }
            section.appendChild(ul);
        }

        el.appendChild(section);
    }

    function _renderDevelopment(el) {
        const data = _resultsData.development_roadmap;

        const header = document.createElement('h3');
        Sanitize.setText(header, 'Development');
        el.appendChild(header);

        // No-roadmap empty state (FR-10)
        if (!data || !data.has_roadmap) {
            const emptyEl = document.createElement('p');
            emptyEl.style.color = 'var(--color-text-muted)';
            emptyEl.style.marginTop = '12px';
            Sanitize.setText(emptyEl, 'No roadmap yet — ask in chat and we’ll build one together.');
            el.appendChild(emptyEl);
            return;
        }

        // Gate block (FR-6, FR-7)
        _renderDevGate(el, data);

        // Practice cards / legacy fallback (FR-4)
        _renderDevPractices(el, data);

        // Journal list (FR-5)
        _renderDevJournal(el, data);
    }

    function _renderReassessment(el) {
        const data = _resultsData.comparison_snapshots;

        // Empty state: visible during reassessment phase before any snapshot is saved,
        // or when called with no data. Explains why the tab is empty and what to do next
        // (frontend-ui-component-states; anti-patterns-happy-path-only).
        if (!data) {
            const emptyEl = document.createElement('div');
            emptyEl.className = 'results-empty-state';
            emptyEl.setAttribute('role', 'status');
            Sanitize.setText(emptyEl,
                'No reassessment yet — finish a development cycle and say ‘I’m ready’ in chat to run your first reassessment.'
            );
            el.appendChild(emptyEl);
            return;
        }

        // Branch header: "Reassessment — Cycle N" for reassessment data;
        // "Check-ins" for check-in data (FR-7).
        const header = document.createElement('h3');
        if (data.kind === 'reassessment') {
            const cycleLabel = data.cycle != null ? ' — Cycle ' + data.cycle : '';
            Sanitize.setText(header, 'Reassessment' + cycleLabel);
        } else {
            Sanitize.setText(header, 'Check-ins');
        }
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
        }

        // NEW: Render detailed regression panel (between date line and comparison charts)
        _renderRegressionDetail(el, data.latest_regression_detail || null);

        // Prefer latest_comparison (API shape) over SSE-direct comparison data
        const compData = data.latest_comparison || null;

        // Side-by-side spider chart comparison
        const prev = (compData && compData.previous_snapshot) || data.previous_snapshot || data.previous;
        const curr = (compData && compData.current_snapshot) || data.current_snapshot || data.current;
        if (prev || curr) {
            _renderComparisonCharts(el, prev, curr);
        }

        // Delta indicators \u2014 prefer latest_comparison.deltas over top-level deltas
        const deltas = (compData && compData.deltas) || data.deltas;
        if (deltas) {
            _renderComparisonDeltas(el, deltas);
        }

        // Quadrant shift indicator \u2014 prefer latest_comparison.quadrant_shift
        const quadrantShift = (compData && compData.quadrant_shift) || data.quadrant_shift;
        if (quadrantShift && quadrantShift.shifted) {
            const shiftEl = document.createElement('div');
            shiftEl.className = 'comparison-shift';
            const fromQ = quadrantShift.previous || quadrantShift.from || '?';
            const toQ = quadrantShift.current || quadrantShift.to || '?';
            Sanitize.setText(shiftEl, 'Quadrant shift: ' + fromQ + ' \u2192 ' + toQ);
            el.appendChild(shiftEl);
        }
    }

    /**
     * Render regression detail panel in one of four states.
     * @param {Element} el - Parent element (Reassessment tab container)
     * @param {Object|null} detail - latest_regression_detail from API, or null
     */
    function _renderRegressionDetail(el, detail) {
        // State 1: No check-in yet / detail not available \u2014 no-op (defensive guard)
        if (!detail) return;

        // State 2: Not evaluated (no graduation baseline or no new check-in)
        if (detail.evaluated === false) {
            const section = document.createElement('section');
            section.className = 'regression-panel regression-panel--unavailable';
            section.setAttribute('role', 'status');
            section.setAttribute('aria-live', 'polite');
            const line = document.createElement('p');
            line.style.margin = '0';
            Sanitize.setText(line, 'Regression comparison unavailable \u2014 ' + (detail.reason || 'no baseline available'));
            section.appendChild(line);
            el.appendChild(section);
            return;
        }

        // State 3: Regression detected
        if (detail.regression_detected === true) {
            const section = document.createElement('section');
            section.className = 'regression-panel';
            section.setAttribute('role', 'status');
            section.setAttribute('aria-live', 'polite');

            const title = document.createElement('div');
            title.className = 'regression-panel__title';
            Sanitize.setText(title, 'Regression Detail');
            section.appendChild(title);

            if (detail.regressed_dimensions && detail.regressed_dimensions.length > 0) {
                const intro = document.createElement('p');
                intro.style.margin = '0 0 6px 0';
                intro.style.fontSize = '14px';
                Sanitize.setText(intro, 'Areas that have slipped since graduation:');
                section.appendChild(intro);

                const ul = document.createElement('ul');
                ul.className = 'regression-panel__dim-list';
                for (const dim of detail.regressed_dimensions) {
                    const li = document.createElement('li');
                    li.className = 'regression-panel__dim';
                    const drop = Number(dim.drop_normalized).toFixed(1);
                    const baseline = Number(dim.baseline_normalized).toFixed(1);
                    const current = Number(dim.current_normalized).toFixed(1);
                    Sanitize.setText(li, '\u25bc ' + dim.dimension + '  \u2212' + drop + ' pts (' + baseline + ' \u2192 ' + current + ')');
                    ul.appendChild(li);
                }
                section.appendChild(ul);
            }

            if (detail.quadrant && detail.quadrant.downgraded) {
                const qdiv = document.createElement('div');
                qdiv.className = 'regression-panel__quadrant-downgrade';
                qdiv.setAttribute('aria-label', 'Quadrant downgraded from ' + detail.quadrant.baseline + ' to ' + detail.quadrant.current);
                Sanitize.setText(qdiv, 'Quadrant: ' + detail.quadrant.baseline + ' \u25be ' + detail.quadrant.current + ' (downgraded)');
                section.appendChild(qdiv);
            }

            const threshold = document.createElement('p');
            threshold.className = 'regression-panel__threshold';
            Sanitize.setText(threshold, 'Threshold: dimensions dropping > ' + Number(detail.threshold_normalized).toFixed(0) + ' pts on the 0\u2013100 scale count as regression.');
            section.appendChild(threshold);

            if (detail.reason) {
                const reason = document.createElement('p');
                reason.className = 'regression-panel__reason';
                Sanitize.setText(reason, detail.reason);
                section.appendChild(reason);
            }

            el.appendChild(section);
            return;
        }

        // State 4: No regression
        const section = document.createElement('section');
        section.className = 'regression-panel regression-panel--clean';
        section.setAttribute('role', 'status');
        section.setAttribute('aria-live', 'polite');

        const headline = document.createElement('p');
        headline.style.margin = '0 0 6px 0';
        Sanitize.setText(headline, 'No regression detected \u2014 all dimensions within threshold and no quadrant downgrade.');
        section.appendChild(headline);

        const threshold = document.createElement('p');
        threshold.className = 'regression-panel__threshold';
        Sanitize.setText(threshold, 'Threshold: dimensions dropping > ' + Number(detail.threshold_normalized).toFixed(0) + ' pts on the 0\u2013100 scale count as regression.');
        section.appendChild(threshold);

        el.appendChild(section);
    }

    function _renderComparisonCharts(el, prev, curr) {
        const grid = document.createElement('div');
        grid.className = 'comparison-grid';

        // Previous panel
        const prevPanel = document.createElement('div');
        prevPanel.className = 'comparison-panel';
        const prevTitle = document.createElement('div');
        prevTitle.className = 'comparison-panel__title';
        Sanitize.setText(prevTitle, 'Previous');
        prevPanel.appendChild(prevTitle);

        if (prev && prev.spider_data && prev.spider_data.image_base64) {
            const img = document.createElement('img');
            img.src = 'data:image/png;base64,' + prev.spider_data.image_base64;
            img.alt = 'Previous spider chart';
            prevPanel.appendChild(img);
        }
        if (prev && prev.quadrant) {
            const q = document.createElement('div');
            q.className = 'comparison-panel__quadrant';
            Sanitize.setText(q, prev.quadrant);
            prevPanel.appendChild(q);
        }
        if (prev && prev.weighted_total !== undefined) {
            const w = document.createElement('div');
            w.style.color = 'var(--color-text-muted)';
            w.style.fontSize = '13px';
            w.style.marginTop = '4px';
            Sanitize.setText(w, 'W: ' + Number(prev.weighted_total).toFixed(1));
            prevPanel.appendChild(w);
        }

        grid.appendChild(prevPanel);

        // Current panel
        const currPanel = document.createElement('div');
        currPanel.className = 'comparison-panel';
        const currTitle = document.createElement('div');
        currTitle.className = 'comparison-panel__title';
        Sanitize.setText(currTitle, 'Current');
        currPanel.appendChild(currTitle);

        if (curr && curr.spider_data && curr.spider_data.image_base64) {
            const img = document.createElement('img');
            img.src = 'data:image/png;base64,' + curr.spider_data.image_base64;
            img.alt = 'Current spider chart';
            currPanel.appendChild(img);
        }
        if (curr && curr.quadrant) {
            const q = document.createElement('div');
            q.className = 'comparison-panel__quadrant';
            Sanitize.setText(q, curr.quadrant);
            currPanel.appendChild(q);
        }
        if (curr && curr.weighted_total !== undefined) {
            const w = document.createElement('div');
            w.style.color = 'var(--color-text-muted)';
            w.style.fontSize = '13px';
            w.style.marginTop = '4px';
            Sanitize.setText(w, 'W: ' + Number(curr.weighted_total).toFixed(1));
            currPanel.appendChild(w);

            // Delta from previous
            if (prev && prev.weighted_total !== undefined) {
                const diff = curr.weighted_total - prev.weighted_total;
                const deltaEl = document.createElement('div');
                deltaEl.style.marginTop = '4px';
                deltaEl.style.fontWeight = '600';
                if (diff > 0) {
                    deltaEl.className = 'comparison-delta__value--up';
                    Sanitize.setText(deltaEl, '\u25B2 +' + diff.toFixed(1));
                } else if (diff < 0) {
                    deltaEl.className = 'comparison-delta__value--down';
                    Sanitize.setText(deltaEl, '\u25BC ' + diff.toFixed(1));
                } else {
                    deltaEl.className = 'comparison-delta__value--neutral';
                    Sanitize.setText(deltaEl, '\u2014 0.0');
                }
                currPanel.appendChild(deltaEl);
            }
        }

        grid.appendChild(currPanel);
        el.appendChild(grid);
    }

    function _renderComparisonDeltas(el, deltas) {
        const deltaHeader = document.createElement('h4');
        deltaHeader.style.marginTop = '12px';
        Sanitize.setText(deltaHeader, 'Score Changes');
        el.appendChild(deltaHeader);

        const container = document.createElement('div');
        container.className = 'comparison-deltas';

        for (const [dim, delta] of Object.entries(deltas)) {
            const row = document.createElement('div');
            row.className = 'comparison-delta';

            const label = document.createElement('span');
            label.className = 'comparison-delta__label';
            Sanitize.setText(label, dim);
            row.appendChild(label);

            const value = document.createElement('span');
            const d = typeof delta === 'object' ? delta.delta : delta;
            const dir = typeof delta === 'object' ? delta.direction : (d > 0 ? 'up' : d < 0 ? 'down' : 'neutral');

            if (dir === 'up') {
                value.className = 'comparison-delta__value comparison-delta__value--up';
                Sanitize.setText(value, '\u25B2 +' + Math.abs(d) + '%');
            } else if (dir === 'down') {
                value.className = 'comparison-delta__value comparison-delta__value--down';
                Sanitize.setText(value, '\u25BC -' + Math.abs(d) + '%');
            } else {
                value.className = 'comparison-delta__value comparison-delta__value--neutral';
                Sanitize.setText(value, '\u2014 0%');
            }
            row.appendChild(value);

            container.appendChild(row);
        }

        el.appendChild(container);
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
        const pct = Math.round(Math.min(Math.max(fraction * 100, 0), 100));
        const bar = document.createElement('div');
        bar.className = 'progress-bar';
        bar.setAttribute('role', 'progressbar');
        bar.setAttribute('aria-valuemin', '0');
        bar.setAttribute('aria-valuemax', '100');
        bar.setAttribute('aria-valuenow', String(pct));
        const fill = document.createElement('div');
        fill.className = 'progress-bar__fill';
        fill.style.width = pct + '%';
        bar.appendChild(fill);
        return bar;
    }

    return {
        update,
        handlePhaseTransition,
        handleSSEEvent,
        renderEarlyResult
    };
})();
