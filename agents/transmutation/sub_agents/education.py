from google.adk.agents import LlmAgent

from agents.transmutation.prompts.education_prompt import PROMPT
from agents.transmutation.tools import (
    get_user_profile,
    get_education_progress,
    record_comprehension_answer,
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


def create_education_agent(model: str = "") -> LlmAgent:
    """Create the Education sub-agent with its tools and prompt."""
    return LlmAgent(
        name="education_agent",
        description=DESCRIPTION,
        instruction=PROMPT,
        model=model,
        tools=[
            get_user_profile,
            get_education_progress,
            record_comprehension_answer,
            advance_phase,
            flag_safety_concern,
        ],
    )
