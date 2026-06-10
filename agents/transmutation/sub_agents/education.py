from typing import Union

from google.adk.agents import LlmAgent
from google.adk.models.base_llm import BaseLlm

from agents.transmutation.prompts.education_prompt import PROMPT
from agents.transmutation.sub_agents.inject_user_id import with_user_id
from agents.transmutation.tools import (
    get_user_profile,
    get_education_progress,
    present_comprehension_question,
    record_comprehension_answer,
    present_continue_prompt,
    advance_phase,
    flag_safety_concern,
)

DESCRIPTION = (
    "Teaches the user about their transmutation profile dimensions. "
    "Covers 5 categories per dimension (what it means, score, daily effects, "
    "strengths/gaps, external interaction), prioritizing weakest dimensions first. "
    "Presents comprehension checks and tracks understanding scores. "
    "Activated when the user is in the education phase."
)


def create_education_agent(model: Union[str, BaseLlm] = "") -> LlmAgent:
    """Create the Education sub-agent with its tools and prompt."""
    return LlmAgent(
        name="education_agent",
        description=DESCRIPTION,
        instruction=with_user_id(PROMPT),
        model=model,
        tools=[
            get_user_profile,
            get_education_progress,
            present_comprehension_question,
            record_comprehension_answer,
            present_continue_prompt,
            advance_phase,
            flag_safety_concern,
        ],
    )
