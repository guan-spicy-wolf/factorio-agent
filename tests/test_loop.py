"""Tests for the agent loop.

Uses a mock Anthropic client to test the loop logic
without making real API calls.
"""

import json
import pytest
from unittest.mock import MagicMock, patch
from types import SimpleNamespace

from agent.loop import run, Budget
from agent.tools import ToolRegistry, tool


# --- Helpers ---

def _make_text_block(text: str):
    return SimpleNamespace(type="text", text=text)


def _make_tool_block(tool_id: str, name: str, input_data: dict):
    return SimpleNamespace(type="tool_use", id=tool_id, name=name, input=input_data)


def _make_response(content_blocks, input_tokens=100, output_tokens=50):
    return SimpleNamespace(
        content=content_blocks,
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
    )


def _build_test_tools():
    @tool
    def echo(message: str) -> str:
        """Echo a message back."""
        return message

    @tool
    def add(a: int, b: int) -> dict:
        """Add two numbers."""
        return {"result": a + b}

    reg = ToolRegistry()
    reg.register(echo)
    reg.register(add)
    return reg


# --- Budget ---

class TestBudget:
    def test_not_exhausted(self):
        b = Budget(max_iterations=10)
        assert not b.exhausted

    def test_exhausted(self):
        b = Budget(max_iterations=2, iterations=2)
        assert b.exhausted

    def test_summary(self):
        b = Budget(max_iterations=10, iterations=3, input_tokens=500, output_tokens=200)
        s = b.summary
        assert s["iterations"] == 3
        assert s["total_tokens"] == 700


# --- Loop ---

class TestLoop:
    @patch("agent.loop.anthropic.Anthropic")
    def test_simple_text_response(self, mock_cls):
        """Agent responds with text only → completes immediately."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        mock_client.messages.create.return_value = _make_response(
            [_make_text_block("Task complete!")]
        )

        tools = _build_test_tools()
        result = run(
            task="Do something",
            system_prompt="You are a test agent.",
            tools=tools,
        )

        assert result.status == "completed"
        assert result.summary == "Task complete!"
        assert result.budget["iterations"] == 1

    @patch("agent.loop.anthropic.Anthropic")
    def test_tool_call_then_response(self, mock_cls):
        """Agent calls a tool, gets result, then responds with text."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        # First call: tool use
        # Second call: text response
        mock_client.messages.create.side_effect = [
            _make_response([
                _make_tool_block("tc1", "echo", {"message": "hello"}),
            ]),
            _make_response([
                _make_text_block("Done! Echo returned: hello"),
            ]),
        ]

        tools = _build_test_tools()
        result = run(
            task="Test echo",
            system_prompt="You are a test agent.",
            tools=tools,
        )

        assert result.status == "completed"
        assert result.budget["iterations"] == 2
        # Verify tool was called
        assert len(result.messages) == 4  # user, assistant+tool_use, user+tool_result, assistant

    @patch("agent.loop.anthropic.Anthropic")
    def test_budget_exhaustion(self, mock_cls):
        """Agent keeps calling tools until budget runs out."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        # Always return a tool call
        mock_client.messages.create.return_value = _make_response([
            _make_tool_block("tc", "echo", {"message": "loop"}),
        ])

        tools = _build_test_tools()
        result = run(
            task="Loop forever",
            system_prompt="You are a test agent.",
            tools=tools,
            max_iterations=3,
        )

        assert result.status == "budget_exhausted"
        assert result.budget["iterations"] == 3

    @patch("agent.loop.anthropic.Anthropic")
    def test_multiple_tool_calls(self, mock_cls):
        """Agent makes multiple tool calls in one response."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        mock_client.messages.create.side_effect = [
            _make_response([
                _make_text_block("Let me do two things."),
                _make_tool_block("tc1", "echo", {"message": "first"}),
                _make_tool_block("tc2", "add", {"a": 1, "b": 2}),
            ]),
            _make_response([
                _make_text_block("Both tools worked!"),
            ]),
        ]

        tools = _build_test_tools()
        result = run(
            task="Multi-tool test",
            system_prompt="You are a test agent.",
            tools=tools,
        )

        assert result.status == "completed"
        # Check tool results message has both results
        tool_result_msg = result.messages[2]
        assert tool_result_msg["role"] == "user"
        assert len(tool_result_msg["content"]) == 2

    @patch("agent.loop.anthropic.Anthropic")
    def test_memory_context_included(self, mock_cls):
        """Memory context is included in the first user message."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        mock_client.messages.create.return_value = _make_response(
            [_make_text_block("Got it.")]
        )

        tools = _build_test_tools()
        result = run(
            task="Test task",
            system_prompt="Test",
            tools=tools,
            memory_context="Previous notes here",
        )

        # Verify the first user message contains both memory and task
        first_msg = result.messages[0]
        assert "<memory>" in first_msg["content"]
        assert "Previous notes here" in first_msg["content"]
        assert "<task>" in first_msg["content"]

    @patch("agent.loop.anthropic.Anthropic")
    def test_api_error_handling(self, mock_cls):
        """API errors are caught and returned."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        mock_client.messages.create.side_effect = Exception("Connection failed")

        tools = _build_test_tools()

        # The loop catches anthropic.APIError specifically,
        # but a generic Exception would propagate. Let's test APIError.
        import anthropic as anth

        mock_client.messages.create.side_effect = anth.APIConnectionError(
            request=MagicMock()
        )

        result = run(
            task="Fail",
            system_prompt="Test",
            tools=tools,
        )

        assert result.status == "error"
        assert result.error is not None
