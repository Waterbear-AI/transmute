from typing import Union

from google.adk.agents import LlmAgent
from google.adk.models.base_llm import BaseLlm

from agents.transmutation.prompts.profile_prompt import PROMPT
from agents.transmutation.sub_agents.inject_user_id import with_user_id
from agents.transmutation.tools import (
    get_user_profile,
    generate_profile_snapshot,
    save_profile_snapshot,
    advance_phase,
    flag_safety_concern,
)

DESCRIPTION = (
    "Interprets the user's completed assessment results. Generates and explains "
    "their profile scores, spider chart, and quadrant placement using no-shame framing. "
    "Highlights cross-dimensional insights and transitions to education phase when ready."
)


def create_profile_agent(model: Union[str, BaseLlm] = "") -> LlmAgent:
    """Create the Profile sub-agent with its tools and prompt."""
    return LlmAgent(
        name="profile_agent",
        description=DESCRIPTION,
        instruction=with_user_id(PROMPT),
        model=model,
        tools=[
            get_user_profile,
            generate_profile_snapshot,
            save_profile_snapshot,
            advance_phase,
            flag_safety_concern,
        ],
    )
