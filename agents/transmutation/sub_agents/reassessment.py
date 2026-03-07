from google.adk.agents import LlmAgent

from agents.transmutation.prompts.reassessment_prompt import PROMPT
from agents.transmutation.tools import (
    get_user_profile,
    get_assessment_state,
    get_development_roadmap,
    present_question_batch,
    save_assessment_response,
    generate_comparison_snapshot,
    save_profile_snapshot,
    evaluate_graduation_readiness,
    advance_phase,
    flag_safety_concern,
)

DESCRIPTION = (
    "Runs targeted reassessment of dimensions from the user's development roadmap, "
    "plus sentinel check-ins on stale dimensions. Compares results against previous "
    "profile snapshot, evaluates graduation readiness, and updates the profile. "
    "Activated when the user is in the reassessment phase."
)


def create_reassessment_agent(model: str = "") -> LlmAgent:
    """Create the Reassessment sub-agent with its tools and prompt."""
    return LlmAgent(
        name="reassessment_agent",
        description=DESCRIPTION,
        instruction=PROMPT,
        model=model,
        tools=[
            get_user_profile,
            get_assessment_state,
            get_development_roadmap,
            present_question_batch,
            save_assessment_response,
            generate_comparison_snapshot,
            save_profile_snapshot,
            evaluate_graduation_readiness,
            advance_phase,
            flag_safety_concern,
        ],
    )
