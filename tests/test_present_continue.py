"""Unit tests for present_continue_prompt tool.

Covers:
  - Default label/message when called with no args
  - Explicit label/message pass through unchanged
  - Empty / whitespace-only values fall back to defaults
  - Payload shape is exactly {event_type, label, message} with the
    education.continue event_type (no extra/leaking fields)
"""

from agents.transmutation.tools import present_continue_prompt


class TestPresentContinuePromptDefaults:
    def test_defaults_event_type(self):
        result = present_continue_prompt()
        assert result["event_type"] == "education.continue"

    def test_defaults_label_and_message(self):
        result = present_continue_prompt()
        assert result["label"] == "Continue"
        assert result["message"] == "continue"


class TestPresentContinuePromptExplicit:
    def test_label_passthrough(self):
        result = present_continue_prompt(label="Continue to Category 2: Your Score")
        assert result["label"] == "Continue to Category 2: Your Score"

    def test_message_passthrough(self):
        result = present_continue_prompt(
            label="Next",
            message="Yes, continue to Category 2: Your Score",
        )
        assert result["message"] == "Yes, continue to Category 2: Your Score"

    def test_strips_surrounding_whitespace(self):
        result = present_continue_prompt(label="  Onward  ", message="  go  ")
        assert result["label"] == "Onward"
        assert result["message"] == "go"


class TestPresentContinuePromptFallbacks:
    def test_empty_label_falls_back(self):
        result = present_continue_prompt(label="", message="ok")
        assert result["label"] == "Continue"
        assert result["message"] == "ok"

    def test_whitespace_only_falls_back(self):
        result = present_continue_prompt(label="   ", message="   ")
        assert result["label"] == "Continue"
        assert result["message"] == "continue"


class TestPresentContinuePromptShape:
    def test_payload_keys_are_exactly_event_label_message(self):
        result = present_continue_prompt(label="X", message="y")
        assert set(result.keys()) == {"event_type", "label", "message"}

    def test_no_answer_or_secret_fields(self):
        # The continue payload carries no question/answer data — it must never
        # surface anything beyond the button label and the reply message.
        result = present_continue_prompt()
        for leaky in ("correct_option", "explanation", "options", "question_id"):
            assert leaky not in result
