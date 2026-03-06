from google.adk.agents import LlmAgent

from agents.transmutation.prompts.assessment_prompt import PROMPT
from agents.transmutation.tools import (
    get_assessment_state,
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


def create_assessment_agent(model: str = "") -> LlmAgent:
    """Create the Assessment sub-agent with its tools and prompt."""
    return LlmAgent(
        name="assessment_agent",
        description=DESCRIPTION,
        instruction=PROMPT,
        model=model,
        tools=[
            get_assessment_state,
            present_question_batch,
            present_scenario,
            save_assessment_response,
            save_scenario_response,
            advance_phase,
            flag_safety_concern,
        ],
    )
