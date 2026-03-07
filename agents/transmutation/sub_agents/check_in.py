from google.adk.agents import LlmAgent

from agents.transmutation.prompts.check_in_prompt import PROMPT
from agents.transmutation.tools import (
    get_user_profile,
    get_assessment_state,
    get_graduation_record,
    present_question_batch,
    save_assessment_response,
    generate_comparison_snapshot,
    save_profile_snapshot,
    save_check_in_log,
    advance_phase,
    flag_safety_concern,
)

DESCRIPTION = (
    "Runs post-graduation check-in assessments. Performs full reassessment "
    "(all dimensions), compares against graduation baseline snapshot, "
    "detects regression, and offers optional re-entry to development. "
    "Activated when the user initiates a check-in after graduation."
)


def create_check_in_agent(model: str = "") -> LlmAgent:
    """Create the Check-in sub-agent with its tools and prompt."""
    return LlmAgent(
        name="check_in_agent",
        description=DESCRIPTION,
        instruction=PROMPT,
        model=model,
        tools=[
            get_user_profile,
            get_assessment_state,
            get_graduation_record,
            present_question_batch,
            save_assessment_response,
            generate_comparison_snapshot,
            save_profile_snapshot,
            save_check_in_log,
            advance_phase,
            flag_safety_concern,
        ],
    )
