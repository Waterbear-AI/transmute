from google.adk.agents import LlmAgent

from agents.transmutation.prompts.profile_prompt import PROMPT
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


def create_profile_agent(model: str = "") -> LlmAgent:
    """Create the Profile sub-agent with its tools and prompt."""
    return LlmAgent(
        name="profile_agent",
        description=DESCRIPTION,
        instruction=PROMPT,
        model=model,
        tools=[
            get_user_profile,
            generate_profile_snapshot,
            save_profile_snapshot,
            advance_phase,
            flag_safety_concern,
        ],
    )
