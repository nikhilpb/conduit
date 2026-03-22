import json

from google.adk.events.event import Event
from google.adk.flows.llm_flows.functions import (
    REQUEST_CONFIRMATION_FUNCTION_CALL_NAME,
)
from google.genai import types

from conduit.context_estimate import build_context_estimate
from conduit.context_estimate import estimate_events_context
from conduit.context_estimate import estimate_tool_call_chars
from conduit.context_estimate import estimate_tool_result_chars


def test_estimate_counts_user_and_assistant_text():
    events = [
        Event(
            invocation_id="inv-user",
            author="user",
            content=types.Content(
                role="user",
                parts=[types.Part(text="Hello from Zurich")],
            ),
        ),
        Event(
            invocation_id="inv-model",
            author="conduit",
            content=types.Content(
                role="model",
                parts=[types.Part(text="Swiss trains are punctual.")],
            ),
        ),
    ]

    estimate = estimate_events_context(events)

    expected_chars = len("Hello from Zurich") + len("Swiss trains are punctual.")
    assert estimate == build_context_estimate(expected_chars)


def test_estimate_counts_tool_calls_and_tool_results():
    tool_args = {"query": "zurich weather", "max_results": 3}
    tool_response = {
        "ok": True,
        "content": "1. MeteoSwiss - Zurich 8C",
        "source": "brave",
    }
    events = [
        Event(
            invocation_id="inv-call",
            author="conduit",
            content=types.Content(
                role="model",
                parts=[
                    types.Part(
                        function_call=types.FunctionCall(
                            id="tc_1",
                            name="web_search",
                            args=tool_args,
                        )
                    )
                ],
            ),
        ),
        Event(
            invocation_id="inv-result",
            author="web_search",
            content=types.Content(
                role="tool",
                parts=[
                    types.Part(
                        function_response=types.FunctionResponse(
                            id="tc_1",
                            name="web_search",
                            response=tool_response,
                        )
                    )
                ],
            ),
        ),
    ]

    estimate = estimate_events_context(events)

    expected_chars = estimate_tool_call_chars("web_search", tool_args) + (
        estimate_tool_result_chars("web_search", tool_response)
    )
    assert estimate == build_context_estimate(expected_chars)


def test_estimate_excludes_internal_calls_and_thinking_trace():
    events = [
        Event(
            invocation_id="inv-model",
            author="conduit",
            content=types.Content(
                role="model",
                parts=[
                    types.Part(text="Hidden chain of thought", thought=True),
                    types.Part(
                        function_call=types.FunctionCall(
                            id="approval-1",
                            name=REQUEST_CONFIRMATION_FUNCTION_CALL_NAME,
                            args={"originalFunctionCall": {"name": "bash"}},
                        )
                    ),
                    types.Part(text="Visible answer"),
                ],
            ),
        )
    ]

    estimate = estimate_events_context(events)

    assert estimate == build_context_estimate(len("Visible answer"))


def test_build_context_estimate_rounds_tokens_from_chars():
    assert build_context_estimate(0).tokens == 0
    assert build_context_estimate(1).tokens == 1
    assert build_context_estimate(4).tokens == 1
    assert build_context_estimate(5).tokens == 2


def test_tool_char_helpers_use_compact_sorted_json():
    args = {"b": 2, "a": 1}
    payload = json.dumps(args, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

    assert estimate_tool_call_chars("web_search", args) == len("web_search") + len(
        payload
    )
    assert estimate_tool_result_chars("web_search", args) == len("web_search") + len(
        payload
    )
