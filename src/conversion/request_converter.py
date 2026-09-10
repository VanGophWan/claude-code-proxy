import json
from typing import Dict, Any, List
from venv import logger
from src.core.constants import Constants
from src.models.claude import ClaudeMessagesRequest, ClaudeMessage
from src.core.config import config
import logging

logger = logging.getLogger(__name__)


def _content_to_text(content) -> str:
    """Flatten a Claude content value (str or list of text blocks) into a single string."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == Constants.CONTENT_TEXT:
                    text_parts.append(block.get("text", ""))
            elif hasattr(block, "type") and block.type == Constants.CONTENT_TEXT:
                text_parts.append(block.text)
        return "\n\n".join(text_parts)
    return ""


def _extract_system_text(claude_request: ClaudeMessagesRequest) -> str:
    """Collect the system prompt from the top-level `system` field and any
    `system`-role messages, combining them in order."""
    parts = []
    if claude_request.system:
        parts.append(_content_to_text(claude_request.system))
    for msg in claude_request.messages:
        if msg.role == Constants.ROLE_SYSTEM:
            parts.append(_content_to_text(msg.content))
    return "\n\n".join(p for p in parts if p and p.strip()).strip()


def convert_claude_to_openai(
    claude_request: ClaudeMessagesRequest, model_manager
) -> Dict[str, Any]:
    """Convert Claude API request format to OpenAI format."""

    # Map model
    openai_model = model_manager.map_claude_model_to_openai(claude_request.model)

    # Extract system prompt from top-level `system` field and system-role messages.
    # Some OpenAI-compatible providers (e.g. Ollama, certain local models) reject
    # both `role: "system"` in the messages array and a top-level `system` key in
    # the request body.  To maximise compatibility, we fold the system prompt into
    # the first user message instead.
    system_text = _extract_system_text(claude_request)

    # Convert messages (skip system-role messages — they're folded into system_text)
    messages = [
        msg for msg in claude_request.messages if msg.role != Constants.ROLE_SYSTEM
    ]
    openai_messages = []

    i = 0
    while i < len(messages):
        msg = messages[i]

        if msg.role == Constants.ROLE_USER:
            openai_message = convert_claude_user_message(msg)
            # Prepend system text to the first user message for providers that
            # do not support a separate system parameter.
            if system_text and not openai_messages:
                if isinstance(openai_message["content"], str):
                    openai_message["content"] = f"{system_text}\n\n{openai_message['content']}"
                elif isinstance(openai_message["content"], list):
                    openai_message["content"].insert(
                        0, {"type": "text", "text": f"{system_text}\n\n"}
                    )
                system_text = ""  # Consumed — don't add as top-level param
            openai_messages.append(openai_message)
        elif msg.role == Constants.ROLE_ASSISTANT:
            openai_message = convert_claude_assistant_message(msg)
            openai_messages.append(openai_message)

            # Check if next message contains tool results
            if i + 1 < len(messages):
                next_msg = messages[i + 1]
                if (
                    next_msg.role == Constants.ROLE_USER
                    and isinstance(next_msg.content, list)
                    and any(
                        block.type == Constants.CONTENT_TOOL_RESULT
                        for block in next_msg.content
                        if hasattr(block, "type")
                    )
                ):
                    # Process tool results
                    i += 1  # Skip to tool result message
                    tool_results = convert_claude_tool_results(next_msg)
                    openai_messages.extend(tool_results)

        i += 1

    # Build OpenAI request
    openai_request = {
        "model": openai_model,
        "messages": openai_messages,
        "max_tokens": min(
            max(claude_request.max_tokens, config.min_tokens_limit),
            config.max_tokens_limit,
        ),
        "temperature": claude_request.temperature,
        "stream": claude_request.stream,
    }
    # If the only user message was a tool-result, system_text may not have been
    # consumed above.  Try the top-level "system" parameter as a fallback.
    if system_text:
        openai_request["system"] = system_text
    logger.debug(
        f"Converted Claude request to OpenAI format: {json.dumps(openai_request, indent=2, ensure_ascii=False)}"
    )
    # Add optional parameters
    if claude_request.stop_sequences:
        openai_request["stop"] = claude_request.stop_sequences
    if claude_request.top_p is not None:
        openai_request["top_p"] = claude_request.top_p

    # Convert tools
    if claude_request.tools:
        openai_tools = []
        for tool in claude_request.tools:
            if tool.name and tool.name.strip():
                openai_tools.append(
                    {
                        "type": Constants.TOOL_FUNCTION,
                        Constants.TOOL_FUNCTION: {
                            "name": tool.name,
                            "description": tool.description or "",
                            "parameters": tool.input_schema,
                        },
                    }
                )
        if openai_tools:
            openai_request["tools"] = openai_tools

    # Convert tool choice
    if claude_request.tool_choice:
        choice_type = claude_request.tool_choice.get("type")
        if choice_type == "auto":
            openai_request["tool_choice"] = "auto"
        elif choice_type == "any":
            openai_request["tool_choice"] = "auto"
        elif choice_type == "tool" and "name" in claude_request.tool_choice:
            openai_request["tool_choice"] = {
                "type": Constants.TOOL_FUNCTION,
                Constants.TOOL_FUNCTION: {"name": claude_request.tool_choice["name"]},
            }
        else:
            openai_request["tool_choice"] = "auto"

    return openai_request


def convert_claude_user_message(msg: ClaudeMessage) -> Dict[str, Any]:
    """Convert Claude user message to OpenAI format."""
    if msg.content is None:
        return {"role": Constants.ROLE_USER, "content": ""}
    
    if isinstance(msg.content, str):
        return {"role": Constants.ROLE_USER, "content": msg.content}

    # Handle multimodal content
    openai_content = []
    for block in msg.content:
        if block.type == Constants.CONTENT_TEXT:
            openai_content.append({"type": "text", "text": block.text})
        elif block.type == Constants.CONTENT_IMAGE:
            # Convert Claude image format to OpenAI format
            if (
                isinstance(block.source, dict)
                and block.source.get("type") == "base64"
                and "media_type" in block.source
                and "data" in block.source
            ):
                openai_content.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{block.source['media_type']};base64,{block.source['data']}"
                        },
                    }
                )

    if len(openai_content) == 1 and openai_content[0]["type"] == "text":
        return {"role": Constants.ROLE_USER, "content": openai_content[0]["text"]}
    else:
        return {"role": Constants.ROLE_USER, "content": openai_content}


def convert_claude_assistant_message(msg: ClaudeMessage) -> Dict[str, Any]:
    """Convert Claude assistant message to OpenAI format."""
    text_parts = []
    tool_calls = []

    if msg.content is None:
        return {"role": Constants.ROLE_ASSISTANT, "content": None}
    
    if isinstance(msg.content, str):
        return {"role": Constants.ROLE_ASSISTANT, "content": msg.content}

    for block in msg.content:
        if block.type == Constants.CONTENT_TEXT:
            text_parts.append(block.text)
        elif block.type == Constants.CONTENT_TOOL_USE:
            tool_calls.append(
                {
                    "id": block.id,
                    "type": Constants.TOOL_FUNCTION,
                    Constants.TOOL_FUNCTION: {
                        "name": block.name,
                        "arguments": json.dumps(block.input, ensure_ascii=False),
                    },
                }
            )

    openai_message = {"role": Constants.ROLE_ASSISTANT}

    # Set content
    if text_parts:
        openai_message["content"] = "".join(text_parts)
    else:
        openai_message["content"] = None

    # Set tool calls
    if tool_calls:
        openai_message["tool_calls"] = tool_calls

    return openai_message


def convert_claude_tool_results(msg: ClaudeMessage) -> List[Dict[str, Any]]:
    """Convert Claude tool results to OpenAI format."""
    tool_messages = []

    if isinstance(msg.content, list):
        for block in msg.content:
            if block.type == Constants.CONTENT_TOOL_RESULT:
                content = parse_tool_result_content(block.content)
                tool_messages.append(
                    {
                        "role": Constants.ROLE_TOOL,
                        "tool_call_id": block.tool_use_id,
                        "content": content,
                    }
                )

    return tool_messages


def parse_tool_result_content(content):
    """Parse and normalize tool result content into a string format."""
    if content is None:
        return "No content provided"

    if isinstance(content, str):
        return content

    if isinstance(content, list):
        result_parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == Constants.CONTENT_TEXT:
                result_parts.append(item.get("text", ""))
            elif isinstance(item, str):
                result_parts.append(item)
            elif isinstance(item, dict):
                if "text" in item:
                    result_parts.append(item.get("text", ""))
                else:
                    try:
                        result_parts.append(json.dumps(item, ensure_ascii=False))
                    except:
                        result_parts.append(str(item))
        return "\n".join(result_parts).strip()

    if isinstance(content, dict):
        if content.get("type") == Constants.CONTENT_TEXT:
            return content.get("text", "")
        try:
            return json.dumps(content, ensure_ascii=False)
        except:
            return str(content)

    try:
        return str(content)
    except:
        return "Unparseable content"
