"""Root agent orchestration for the Transmutation Engine.

The root agent handles the orientation phase directly and delegates
to sub-agents (assessment, profile) based on the user's current phase.
ADK's built-in agent transfer uses sub-agent descriptions for routing.
"""

from google.adk.agents import LlmAgent

from agents.transmutation.prompts.shared.safety import PROMPT as SAFETY
from agents.transmutation.prompts.shared.boundary import PROMPT as BOUNDARY
from agents.transmutation.prompts.shared.orientation import PROMPT as ORIENTATION
from agents.transmutation.prompts.shared.transmutation_concepts import PROMPT as TRANSMUTATION
from agents.transmutation.sub_agents.assessment import create_assessment_agent
from agents.transmutation.sub_agents.profile import create_profile_agent
from agents.transmutation.sub_agents.education import create_education_agent
from agents.transmutation.sub_agents.development import create_development_agent
from agents.transmutation.sub_agents.reassessment import create_reassessment_agent
from agents.transmutation.sub_agents.graduation import create_graduation_agent
from agents.transmutation.sub_agents.check_in import create_check_in_agent
from agents.transmutation.tools import (
    get_assessment_state,
    get_user_profile,
    advance_phase,
    flag_safety_concern,
)

ROOT_INSTRUCTION = "\n\n".join([
    """## Transmutation Engine — Root Agent

You are the Transmutation Engine, a conversational guide that helps users understand their transmutation patterns — how they handle deprivation and fulfillment in their lives.

**Phase routing:**
You manage the user's journey through phases. Check the user's `current_phase` to determine what to do:

- **orientation**: Handle directly. Greet the user, confirm they've read the overview, ask the grounding question, then call `advance_phase('assessment')`.
- **assessment**: Transfer to the Assessment Agent. It will guide the user through questions and scenarios.
- **profile**: Transfer to the Profile Agent. It will interpret their results and present their profile.
- **education**: Transfer to the Education Agent. It teaches the user about their transmutation dimensions with comprehension checks.
- **development**: Transfer to the Development Agent. It manages roadmaps, practice journaling, and growth.
- **reassessment**: Transfer to the Reassessment Agent. It runs targeted reassessment and evaluates graduation readiness.
- **graduation**: Transfer to the Graduation Agent. It guides the closing sequence.
- **graduated** / **check_in**: Transfer to the Check-in Agent for post-graduation assessment.

**On first message in a new session:**
1. Call `get_assessment_state()` to check if there's existing progress.
2. If the user has a completed profile, offer to review it or start fresh.
3. If the user has partial assessment data, offer to continue where they left off.
4. If no data exists, begin orientation.

**General behavior:**
- Be warm, curious, and supportive.
- Keep messages concise — under 3 paragraphs unless explaining results.
- Never rush the user through phases.
- If the user seems distressed, follow the safety protocol.
""",
    SAFETY,
    BOUNDARY,
    ORIENTATION,
    TRANSMUTATION,
])


def create_transmutation_agent(model: str = "") -> LlmAgent:
    """Create the root Transmutation Engine agent with sub-agents.

    Args:
        model: LLM model identifier (e.g. "claude-sonnet-4-20250514").
               If empty, uses the ADK default.
    """
    assessment_agent = create_assessment_agent(model=model)
    profile_agent = create_profile_agent(model=model)
    education_agent = create_education_agent(model=model)
    development_agent = create_development_agent(model=model)
    reassessment_agent = create_reassessment_agent(model=model)
    graduation_agent = create_graduation_agent(model=model)
    check_in_agent = create_check_in_agent(model=model)

    return LlmAgent(
        name="transmutation_engine",
        description="Root orchestrator for the Transmutation Engine. Routes users through the full lifecycle: orientation, assessment, profile, education, development, reassessment, graduation, and check-in.",
        instruction=ROOT_INSTRUCTION,
        model=model,
        tools=[
            get_assessment_state,
            get_user_profile,
            advance_phase,
            flag_safety_concern,
        ],
        sub_agents=[
            assessment_agent,
            profile_agent,
            education_agent,
            development_agent,
            reassessment_agent,
            graduation_agent,
            check_in_agent,
        ],
    )
