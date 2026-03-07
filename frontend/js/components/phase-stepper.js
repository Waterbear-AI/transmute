/* Transmutation Engine - Phase Stepper Component */
'use strict';

const PhaseStepper = (() => {
    const PHASES = [
        { id: 'orientation', label: 'Orientation', cssVar: '--phase-orientation' },
        { id: 'assessment', label: 'Assessment', cssVar: '--phase-assessment' },
        { id: 'profile', label: 'Profile', cssVar: '--phase-profile' },
        { id: 'education', label: 'Education', cssVar: '--phase-education' },
        { id: 'development', label: 'Development', cssVar: '--phase-development' },
        { id: 'reassessment', label: 'Reassessment', cssVar: '--phase-reassessment' },
        { id: 'graduation', label: 'Graduation', cssVar: '--phase-graduation' }
    ];

    /**
     * Render the phase stepper into a container.
     * @param {HTMLElement} containerEl - DOM element to render into
     * @param {string} currentPhase - The user's current phase id
     * @param {function} onPhaseClick - Callback when a completed phase dot is clicked
     */
    function render(containerEl, currentPhase, onPhaseClick) {
        containerEl.textContent = '';

        const currentIdx = PHASES.findIndex(p => p.id === currentPhase);

        const stepper = document.createElement('div');
        stepper.className = 'phase-stepper';
        stepper.setAttribute('role', 'navigation');
        stepper.setAttribute('aria-label', 'Phase progression');

        // Progress fraction
        const progressLabel = document.createElement('span');
        progressLabel.className = 'phase-stepper__progress';
        Sanitize.setText(progressLabel, 'Phase ' + (currentIdx + 1) + ' of ' + PHASES.length);
        stepper.appendChild(progressLabel);

        const track = document.createElement('div');
        track.className = 'phase-stepper__track';

        for (let i = 0; i < PHASES.length; i++) {
            const phase = PHASES[i];
            const isCompleted = i < currentIdx;
            const isCurrent = i === currentIdx;

            // Connector line (before each dot except the first)
            if (i > 0) {
                const line = document.createElement('div');
                line.className = 'phase-stepper__line';
                if (isCompleted || isCurrent) {
                    line.classList.add('phase-stepper__line--filled');
                }
                track.appendChild(line);
            }

            // Step container (dot + label)
            const step = document.createElement('div');
            step.className = 'phase-stepper__step';

            const dot = document.createElement('button');
            dot.className = 'phase-stepper__dot';
            dot.setAttribute('aria-label', phase.label + (isCompleted ? ' (completed)' : isCurrent ? ' (current)' : ' (upcoming)'));

            const color = getComputedStyle(document.documentElement).getPropertyValue(phase.cssVar).trim();

            if (isCompleted) {
                dot.classList.add('phase-stepper__dot--completed');
                dot.style.backgroundColor = color;
                dot.style.borderColor = color;
                dot.addEventListener('click', () => {
                    if (onPhaseClick) onPhaseClick(phase.id);
                });
            } else if (isCurrent) {
                dot.classList.add('phase-stepper__dot--current');
                dot.style.borderColor = color;
                dot.style.boxShadow = '0 0 0 3px ' + color + '40';
            } else {
                dot.classList.add('phase-stepper__dot--upcoming');
                dot.disabled = true;
            }

            step.appendChild(dot);

            const label = document.createElement('span');
            label.className = 'phase-stepper__label';
            if (isCurrent) label.classList.add('phase-stepper__label--current');
            Sanitize.setText(label, phase.label);
            step.appendChild(label);

            track.appendChild(step);
        }

        stepper.appendChild(track);
        containerEl.appendChild(stepper);
    }

    return { render };
})();
