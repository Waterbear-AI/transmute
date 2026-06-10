from typing import Union

from google.adk.agents import LlmAgent
from google.adk.models.base_llm import BaseLlm

from agents.transmutation.prompts.assessment_prompt import PROMPT
from agents.transmutation.sub_agents.inject_user_id import with_user_id
from agents.transmutation.tools import (
    get_assessment_state,
    get_next_question_batch,
    present_question_batch,
    present_scenario,
    save_assessment_response,
    save_scenario_response,
    advance_phase,
    flag_safety_concern,
)

DESCRIPTION = (
    "Guides users through the transmutation awareness assessment. "
    "Presents Likert-scale questions grouped by dimension and behavioral scenarios. "
    "Handles pacing, save points, N/A responses, and transitions to profile generation "
    "when all dimensions have sufficient coverage."
)


def create_assessment_agent(model: Union[str, BaseLlm] = "") -> LlmAgent:
    """Create the Assessment sub-agent with its tools and prompt."""
    return LlmAgent(
        name="assessment_agent",
        description=DESCRIPTION,
        instruction=with_user_id(PROMPT),
        model=model,
        tools=[
            get_assessment_state,
            get_next_question_batch,
            present_question_batch,
            present_scenario,
            save_assessment_response,
            save_scenario_response,
            advance_phase,
            flag_safety_concern,
        ],
    )
