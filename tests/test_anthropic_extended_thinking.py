from anthropic import types as anthropic_types
from google.genai import types

from conduit.anthropic_extended_thinking import _content_block_to_part
from conduit.anthropic_extended_thinking import _part_to_message_block
from conduit.runtime import _extract_text


def test_extract_text_ignores_thought_parts():
    content = types.Content(
        role="model",
        parts=[
            types.Part(
                text="private reasoning",
                thought=True,
                thought_signature=b"sig-123",
            ),
            types.Part(text="public answer"),
        ],
    )

    assert _extract_text(content) == "public answer"


def test_round_trips_thinking_block_via_part():
    thinking_block = anthropic_types.ThinkingBlock(
        type="thinking",
        thinking="hidden chain",
        signature="sig-123",
    )

    part = _content_block_to_part(thinking_block)

    assert part.thought is True
    assert part.text == "hidden chain"
    assert part.thought_signature == b"sig-123"

    message_block = _part_to_message_block(part)

    assert message_block["type"] == "thinking"
    assert message_block["thinking"] == "hidden chain"
    assert message_block["signature"] == "sig-123"
