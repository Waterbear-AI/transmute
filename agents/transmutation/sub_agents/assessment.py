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
    present_transmute_core_batch,
    evaluate_transmute_core_complete,
    get_next_adaptive_batch,
)

DESCRIPTION = (
    "Guides users through the transmute-first, tiered transmutation assessment. "
    "Presents Transmutation Capacity items and scenarios first (Tier 1, producing an "
    "early result), then adaptive awareness items (Tiers 2-3). "
    "Handles pacing, save points, N/A responses, and transitions to profile generation "
    "once the tiered flow reports assessment_tier == 'complete'."
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
            present_transmute_core_batch,
            evaluate_transmute_core_complete,
            get_next_adaptive_batch,
        ],
    )
