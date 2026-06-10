from typing import Union

from google.adk.agents import LlmAgent
from google.adk.models.base_llm import BaseLlm

from agents.transmutation.prompts.check_in_prompt import PROMPT
from agents.transmutation.sub_agents.inject_user_id import with_user_id
from agents.transmutation.tools import (
    get_user_profile,
    get_assessment_state,
    get_graduation_record,
    get_next_question_batch,
    present_question_batch,
    save_assessment_response,
    generate_check_in_snapshot,
    generate_comparison_snapshot,
    save_profile_snapshot,
    detect_check_in_regression,
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


def create_check_in_agent(model: Union[str, BaseLlm] = "") -> LlmAgent:
    """Create the Check-in sub-agent with its tools and prompt."""
    return LlmAgent(
        name="check_in_agent",
        description=DESCRIPTION,
        instruction=with_user_id(PROMPT),
        model=model,
        tools=[
            get_user_profile,
            get_assessment_state,
            get_graduation_record,
            get_next_question_batch,
            present_question_batch,
            save_assessment_response,
            generate_check_in_snapshot,
            generate_comparison_snapshot,
            save_profile_snapshot,
            detect_check_in_regression,
            save_check_in_log,
            advance_phase,
            flag_safety_concern,
        ],
    )
