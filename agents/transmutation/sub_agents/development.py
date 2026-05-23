from google.adk.agents import LlmAgent

from agents.transmutation.prompts.development_prompt import PROMPT
from agents.transmutation.sub_agents.inject_user_id import with_user_id
from agents.transmutation.tools import (
    get_user_profile,
    get_development_roadmap,
    generate_roadmap,
    rank_gaps,
    save_roadmap,
    log_practice_entry,
    get_practice_history,
    update_roadmap,
    check_roadmap_targets_gaps,
    advance_phase,
    flag_safety_concern,
)

DESCRIPTION = (
    "Guides users through development practices with a structured 3-step roadmap. "
    "Manages practice journaling, tracks self-ratings and trends, and handles "
    "roadmap adjustments with cooldown enforcement. Focuses on building independent "
    "transmutation capacity. Activated when the user is in the development phase."
)


def create_development_agent(model: str = "") -> LlmAgent:
    """Create the Development sub-agent with its tools and prompt."""
    return LlmAgent(
        name="development_agent",
        description=DESCRIPTION,
        instruction=with_user_id(PROMPT),
        model=model,
        tools=[
            get_user_profile,
            get_development_roadmap,
            generate_roadmap,
            rank_gaps,
            save_roadmap,
            log_practice_entry,
            get_practice_history,
            update_roadmap,
            check_roadmap_targets_gaps,
            advance_phase,
            flag_safety_concern,
        ],
    )
