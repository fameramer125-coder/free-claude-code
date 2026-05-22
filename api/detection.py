"""Request detection utilities for API fast-path responses."""

from core.anthropic import extract_text_from_content

from .models.anthropic import MessagesRequest


def is_quota_check_request(request_data: MessagesRequest) -> bool:
    """Return True for quota probe requests (max_tokens=1, single message containing 'quota')."""
    if (
        request_data.max_tokens == 1
        and len(request_data.messages) == 1
        and request_data.messages[0].role == "user"
    ):
        text = extract_text_from_content(request_data.messages[0].content)
        if "quota" in text.lower():
            return True
    return False


def is_title_generation_request(request_data: MessagesRequest) -> bool:
    """Return True for conversation title generation requests (system contains title instructions)."""
    if not request_data.system or request_data.tools:
        return False
    system_text = extract_text_from_content(request_data.system).lower()
    if "title" not in system_text:
        return False
    return "sentence-case title" in system_text or (
        "return json" in system_text
        and "field" in system_text
        and ("coding session" in system_text or "this session" in system_text)
    )


def is_prefix_detection_request(request_data: MessagesRequest) -> tuple[bool, str]:
    """Return (True, command) when the request is a shell command prefix classification."""
    if len(request_data.messages) != 1 or request_data.messages[0].role != "user":
        return False, ""

    content = extract_text_from_content(request_data.messages[0].content)

    if "<policy_spec>" in content and "Command:" in content:
        cmd_start = content.rfind("Command:") + len("Command:")
        return True, content[cmd_start:].strip()

    return False, ""


def is_suggestion_mode_request(request_data: MessagesRequest) -> bool:
    """Return True when the user message contains [SUGGESTION MODE: (auto-suggestion request)."""
    for msg in request_data.messages:
        if msg.role == "user":
            text = extract_text_from_content(msg.content)
            if "[SUGGESTION MODE:" in text:
                return True
    return False


def is_filepath_extraction_request(
    request_data: MessagesRequest,
) -> tuple[bool, str]:
    """Return (True, command) when the request asks to extract file paths from a shell command."""
    if len(request_data.messages) != 1 or request_data.messages[0].role != "user":
        return False, ""
    if request_data.tools:
        return False, ""

    content = extract_text_from_content(request_data.messages[0].content)

    if "Command:" not in content or "Output:" not in content:
        return False, ""

    user_has_filepaths = (
        "filepaths" in content.lower() or "<filepaths>" in content.lower()
    )
    system_text = (
        extract_text_from_content(request_data.system) if request_data.system else ""
    )
    system_has_extract = (
        "extract any file paths" in system_text.lower()
        or "file paths that this command" in system_text.lower()
    )
    if not user_has_filepaths and not system_has_extract:
        return False, ""

    cmd_start = content.find("Command:") + len("Command:")
    output_marker = content.find("Output:", cmd_start)
    if output_marker == -1:
        return False, ""

    command = content[cmd_start:output_marker].strip()
    return True, command
