"""Anthropic model wrapper with manual extended thinking enabled."""

from __future__ import annotations

import base64
from typing import AsyncGenerator
from typing import Iterable
from typing import Literal
from typing import Optional
from typing import Union

from anthropic import NOT_GIVEN
from anthropic import types as anthropic_types
from google.adk.models.anthropic_llm import AnthropicLlm
from google.adk.models.anthropic_llm import _is_image_part
from google.adk.models.anthropic_llm import function_declaration_to_tool_param
from google.adk.models.anthropic_llm import to_claude_role
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from typing_extensions import override

_REDACTED_THINKING_PREFIX = b"anthropic-redacted-thinking:"
_INTERLEAVED_THINKING_BETA = "interleaved-thinking-2025-05-14"


def _encode_redacted_thinking(data: str) -> bytes:
    return _REDACTED_THINKING_PREFIX + data.encode("utf-8")


def _decode_redacted_thinking(signature: bytes | None) -> str | None:
    if not signature or not signature.startswith(_REDACTED_THINKING_PREFIX):
        return None
    return signature[len(_REDACTED_THINKING_PREFIX) :].decode("utf-8")


def _part_to_message_block(
    part: types.Part,
) -> Union[
    anthropic_types.TextBlockParam,
    anthropic_types.ImageBlockParam,
    anthropic_types.ToolUseBlockParam,
    anthropic_types.ToolResultBlockParam,
    anthropic_types.ThinkingBlockParam,
    anthropic_types.RedactedThinkingBlockParam,
]:
    if part.thought:
        redacted_data = _decode_redacted_thinking(part.thought_signature)
        if redacted_data is not None:
            return anthropic_types.RedactedThinkingBlockParam(
                data=redacted_data,
                type="redacted_thinking",
            )

        signature = (
            part.thought_signature.decode("utf-8")
            if part.thought_signature
            else ""
        )
        return anthropic_types.ThinkingBlockParam(
            signature=signature,
            thinking=part.text or "",
            type="thinking",
        )

    if part.text:
        return anthropic_types.TextBlockParam(text=part.text, type="text")
    if part.function_call:
        assert part.function_call.name
        return anthropic_types.ToolUseBlockParam(
            id=part.function_call.id or "",
            name=part.function_call.name,
            input=part.function_call.args,
            type="tool_use",
        )
    if part.function_response:
        content = ""
        response_data = part.function_response.response

        if "content" in response_data and response_data["content"]:
            content_items = []
            for item in response_data["content"]:
                if isinstance(item, dict):
                    if item.get("type") == "text" and "text" in item:
                        content_items.append(item["text"])
                    else:
                        content_items.append(str(item))
                else:
                    content_items.append(str(item))
            content = "\n".join(content_items) if content_items else ""
        elif "result" in response_data and response_data["result"]:
            content = str(response_data["result"])

        return anthropic_types.ToolResultBlockParam(
            tool_use_id=part.function_response.id or "",
            type="tool_result",
            content=content,
            is_error=False,
        )
    if _is_image_part(part):
        data = base64.b64encode(part.inline_data.data).decode()
        return anthropic_types.ImageBlockParam(
            type="image",
            source=dict(
                type="base64",
                media_type=part.inline_data.mime_type,
                data=data,
            ),
        )
    if part.executable_code:
        return anthropic_types.TextBlockParam(
            type="text",
            text="Code:```python\n" + part.executable_code.code + "\n```",
        )
    if part.code_execution_result:
        return anthropic_types.TextBlockParam(
            text=(
                "Execution Result:```code_output\n"
                + part.code_execution_result.output
                + "\n```"
            ),
            type="text",
        )

    raise NotImplementedError(f"Not supported yet: {part}")


def _content_to_message_param(content: types.Content) -> anthropic_types.MessageParam:
    message_blocks = []
    for part in content.parts or []:
        if content.role != "user" and _is_image_part(part):
            continue
        message_blocks.append(_part_to_message_block(part))

    return {
        "role": to_claude_role(content.role),
        "content": message_blocks,
    }


def _content_block_to_part(content_block: anthropic_types.ContentBlock) -> types.Part:
    if isinstance(content_block, anthropic_types.TextBlock):
        return types.Part.from_text(text=content_block.text)
    if isinstance(content_block, anthropic_types.ToolUseBlock):
        assert isinstance(content_block.input, dict)
        part = types.Part.from_function_call(
            name=content_block.name,
            args=content_block.input,
        )
        part.function_call.id = content_block.id
        return part
    if isinstance(content_block, anthropic_types.ThinkingBlock):
        return types.Part(
            text=content_block.thinking,
            thought=True,
            thought_signature=content_block.signature.encode("utf-8"),
        )
    if isinstance(content_block, anthropic_types.RedactedThinkingBlock):
        return types.Part(
            text="",
            thought=True,
            thought_signature=_encode_redacted_thinking(content_block.data),
        )

    raise NotImplementedError(f"Unsupported Anthropic content block: {type(content_block)!r}")


def _message_to_llm_response(message: anthropic_types.Message) -> LlmResponse:
    return LlmResponse(
        content=types.Content(
            role="model",
            parts=[_content_block_to_part(content_block) for content_block in message.content],
        ),
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=message.usage.input_tokens,
            candidates_token_count=message.usage.output_tokens,
            total_token_count=message.usage.input_tokens + message.usage.output_tokens,
        ),
    )


class ConduitAnthropicLlm(AnthropicLlm):
    """Anthropic ADK adapter with manual extended thinking enabled."""

    thinking_budget_tokens: int = 2048
    interleaved_thinking: bool = True

    @override
    async def generate_content_async(
        self,
        llm_request,
        stream: bool = False,
    ) -> AsyncGenerator[LlmResponse, None]:
        del stream  # Anthropic streaming is not used in this runtime.

        messages = [
            _content_to_message_param(content)
            for content in llm_request.contents or []
        ]

        tools = NOT_GIVEN
        if (
            llm_request.config
            and llm_request.config.tools
            and llm_request.config.tools[0].function_declarations
        ):
            tools = [
                function_declaration_to_tool_param(tool)
                for tool in llm_request.config.tools[0].function_declarations
            ]

        tool_choice = (
            anthropic_types.ToolChoiceAutoParam(type="auto")
            if llm_request.tools_dict
            else NOT_GIVEN
        )
        system_instruction: Union[str, object] = NOT_GIVEN
        if llm_request.config and llm_request.config.system_instruction:
            system_instruction = llm_request.config.system_instruction

        extra_headers = None
        model_name = (llm_request.model or "").lower()
        if (
            self.interleaved_thinking
            and llm_request.tools_dict
            and model_name.startswith("claude-sonnet-4-6")
        ):
            extra_headers = {"anthropic-beta": _INTERLEAVED_THINKING_BETA}

        message = await self._anthropic_client.messages.create(
            model=llm_request.model,
            system=system_instruction,
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            max_tokens=self.max_tokens,
            thinking={
                "type": "enabled",
                "budget_tokens": self.thinking_budget_tokens,
            },
            extra_headers=extra_headers,
        )
        yield _message_to_llm_response(message)
