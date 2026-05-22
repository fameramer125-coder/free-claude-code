"""Edge case tests for api/detection.py."""

from api.detection import (
    is_filepath_extraction_request,
    is_prefix_detection_request,
)
from api.models.anthropic import Message, MessagesRequest


def _make_request(content: str, **kwargs) -> MessagesRequest:
    return MessagesRequest(
        model="claude-3-sonnet",
        max_tokens=100,
        messages=[Message(role="user", content=content)],
        **kwargs,
    )


class TestIsPrefixDetectionRequest:
    def test_output_marker_handling(self):
        """Content with Command: but Output: after cmd_start; output has < or \\n\\n."""
        content = "<policy_spec> Command:\nls -la\nOutput:\na.txt\nb.txt\n\nmore"
        req = _make_request(content)
        is_req, cmd = is_prefix_detection_request(req)
        assert is_req is True
        assert "ls -la" in cmd

    def test_prefix_detection_with_empty_command_section(self):
        """Command: at end with no content returns empty command."""
        req = _make_request("<policy_spec> Command: ")
        is_req, cmd = is_prefix_detection_request(req)
        assert is_req is True
        assert cmd == ""


class TestIsFilepathExtractionRequest:
    def test_output_marker_minus_one_returns_false(self):
        """Output: not found after Command: returns False."""
        content = "Command:\nls\nfilepaths"
        req = _make_request(content)
        is_fp, cmd = is_filepath_extraction_request(req)
        assert is_fp is False
        assert cmd == ""
