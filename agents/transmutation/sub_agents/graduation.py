from google.adk.agents import LlmAgent

from agents.transmutation.prompts.graduation_prompt import PROMPT
from agents.transmutation.tools import (
    get_user_profile,
    get_longitudinal_snapshots,
    generate_graduation_artifacts,
    save_graduation_record,
    save_profile_snapshot,
    generate_profile_snapshot,
    advance_phase,
    flag_safety_concern,
)

DESCRIPTION = (
    "Guides the user through the graduation closing sequence when convergence "
    "indicators are met. Conducts longitudinal review, collaborative pattern "
    "narrative, independent practice map, final snapshot, and check-in invitation. "
    "Activated when the user is in the graduation phase."
)


def create_graduation_agent(model: str = "") -> LlmAgent:
    """Create the Graduation sub-agent with its tools and prompt."""
    return LlmAgent(
        name="graduation_agent",
        description=DESCRIPTION,
        instruction=PROMPT,
        model=model,
        tools=[
            get_user_profile,
            get_longitudinal_snapshots,
            generate_graduation_artifacts,
            save_graduation_record,
            save_profile_snapshot,
            generate_profile_snapshot,
            advance_phase,
            flag_safety_concern,
        ],
    )
