"""Agent main loop: system prompt → LLM API → tool dispatch → repeat.

Supports both Anthropic and OpenAI-compatible APIs.
No streaming, no compaction — just the loop.
"""

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Literal

import anthropic
from openai import OpenAI

from agent.tools import ToolRegistry

logger = logging.getLogger(__name__)

# Provider defaults
DEFAULT_PROVIDER: Literal["anthropic", "openai"] = "anthropic"
DEFAULT_MODEL_ANTHROPIC = "claude-sonnet-4-20250514"
DEFAULT_MODEL_OPENAI = "gpt-4o"
DEFAULT_MAX_ITERATIONS = 100
DEFAULT_MAX_TOKENS = 4096


@dataclass
class Budget:
    """Tracks iteration count and token usage."""

    max_iterations: int = DEFAULT_MAX_ITERATIONS
    iterations: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def exhausted(self) -> bool:
        return self.iterations >= self.max_iterations

    @property
    def summary(self) -> dict:
        return {
            "iterations": self.iterations,
            "max_iterations": self.max_iterations,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.input_tokens + self.output_tokens,
        }


@dataclass
class RunResult:
    """Result of an agent run."""

    status: str  # "completed" | "budget_exhausted" | "error"
    summary: str  # last assistant text
    budget: dict = field(default_factory=dict)
    messages: list[dict] = field(default_factory=list)
    error: str | None = None


def run(
    *,
    task: str,
    system_prompt: str,
    tools: ToolRegistry,
    provider: Literal["anthropic", "openai"] = DEFAULT_PROVIDER,
    model: str | None = None,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    memory_context: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
) -> RunResult:
    """Run the agent loop until completion or budget exhaustion.

    Args:
        task: The goal/task description for the agent.
        system_prompt: System prompt defining agent behavior.
        tools: Registry of callable tools.
        provider: "anthropic" or "openai".
        model: Model ID (defaults based on provider).
        max_iterations: Max LLM round-trips.
        max_tokens: Max tokens per LLM response.
        memory_context: Optional memory content to prepend to task.
        api_key: API key (defaults to env: ANTHROPIC_API_KEY or OPENAI_API_KEY).
        base_url: OpenAI-compatible base URL (e.g. for local endpoints).
    """
    # Set defaults based on provider
    if model is None:
        model = DEFAULT_MODEL_ANTHROPIC if provider == "anthropic" else DEFAULT_MODEL_OPENAI

    budget = Budget(max_iterations=max_iterations)

    # Build initial messages
    messages: list[dict] = []

    # Include memory as initial context if available
    user_content = ""
    if memory_context and memory_context.strip():
        user_content += f"<memory>\n{memory_context}\n</memory>\n\n"
    user_content += f"<task>\n{task}\n</task>"

    messages.append({"role": "user", "content": user_content})

    last_text = ""

    if provider == "anthropic":
        return _run_anthropic(
            task=task,
            system_prompt=system_prompt,
            tools=tools,
            model=model,
            budget=budget,
            messages=messages,
            max_tokens=max_tokens,
            api_key=api_key,
        )
    else:
        return _run_openai(
            task=task,
            system_prompt=system_prompt,
            tools=tools,
            model=model,
            budget=budget,
            messages=messages,
            max_tokens=max_tokens,
            api_key=api_key,
            base_url=base_url,
        )


def _run_anthropic(
    *,
    task: str,
    system_prompt: str,
    tools: ToolRegistry,
    model: str,
    budget: Budget,
    messages: list[dict],
    max_tokens: int,
    api_key: str | None,
) -> RunResult:
    """Run using Anthropic API."""
    client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
    tool_schemas = tools.schemas()
    last_text = ""

    while not budget.exhausted:
        budget.iterations += 1
        logger.info(
            "Iteration %d/%d (tokens: %d in, %d out) [anthropic]",
            budget.iterations,
            budget.max_iterations,
            budget.input_tokens,
            budget.output_tokens,
        )

        # Call LLM
        try:
            response = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system_prompt,
                messages=messages,
                tools=tool_schemas if tool_schemas else anthropic.NOT_GIVEN,
            )
        except anthropic.APIError as e:
            logger.error("API error: %s", e)
            return RunResult(
                status="error",
                summary=last_text,
                budget=budget.summary,
                messages=messages,
                error=str(e),
            )

        # Track tokens
        budget.input_tokens += response.usage.input_tokens
        budget.output_tokens += response.usage.output_tokens

        # Process response content blocks
        assistant_content = []
        tool_calls = []

        for block in response.content:
            if block.type == "text":
                last_text = block.text
                assistant_content.append({"type": "text", "text": block.text})
                logger.info("Assistant: %s", block.text[:200])
            elif block.type == "tool_use":
                tool_calls.append(block)
                assistant_content.append({
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })

        # Append assistant message
        messages.append({"role": "assistant", "content": assistant_content})

        # If no tool calls, agent is done
        if not tool_calls:
            return RunResult(
                status="completed",
                summary=last_text,
                budget=budget.summary,
                messages=messages,
            )

        # Execute tool calls
        tool_results = []
        for tc in tool_calls:
            logger.info("Tool call: %s(%s)", tc.name, json.dumps(tc.input)[:200])

            result = tools.call(tc.name, tc.input)

            # Serialize result to string
            if isinstance(result, str):
                result_str = result
            else:
                result_str = json.dumps(result, ensure_ascii=False, indent=2)

            logger.info("Tool result: %s", result_str[:200])

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tc.id,
                "content": result_str,
            })

        messages.append({"role": "user", "content": tool_results})

    # Budget exhausted
    return RunResult(
        status="budget_exhausted",
        summary=last_text,
        budget=budget.summary,
        messages=messages,
    )


def _run_openai(
    *,
    task: str,
    system_prompt: str,
    tools: ToolRegistry,
    model: str,
    budget: Budget,
    messages: list[dict],
    max_tokens: int,
    api_key: str | None,
    base_url: str | None,
) -> RunResult:
    """Run using OpenAI-compatible API."""
    # Get API key from env if not provided
    if api_key is None:
        api_key = os.environ.get("OPENAI_API_KEY", "")

    client = OpenAI(api_key=api_key, base_url=base_url)

    # Convert tool schemas to OpenAI format
    openai_tools = None
    if tools.schemas():
        openai_tools = []
        for schema in tools.schemas():
            openai_tools.append({
                "type": "function",
                "function": {
                    "name": schema.get("name", ""),
                    "description": schema.get("description", ""),
                    "parameters": schema.get("input_schema", {}),
                }
            })

    last_text = ""

    # Convert messages to OpenAI format (system as separate message)
    openai_messages = [{"role": "system", "content": system_prompt}]
    for msg in messages:
        if msg["role"] == "user":
            content = msg["content"]
            if isinstance(content, str):
                openai_messages.append({"role": "user", "content": content})
            elif isinstance(content, list):
                # Tool results
                for item in content:
                    if item.get("type") == "tool_result":
                        openai_messages.append({
                            "role": "tool",
                            "tool_call_id": item.get("tool_use_id", ""),
                            "content": item.get("content", ""),
                        })
        elif msg["role"] == "assistant":
            content = msg["content"]
            if isinstance(content, str):
                openai_messages.append({"role": "assistant", "content": content})
            elif isinstance(content, list):
                # Assistant with tool calls
                text_parts = [c.get("text", "") for c in content if c.get("type") == "text"]
                tool_calls = [c for c in content if c.get("type") == "tool_use"]

                msg_dict = {"role": "assistant"}
                if text_parts:
                    msg_dict["content"] = "\n".join(text_parts)
                if tool_calls:
                    msg_dict["tool_calls"] = [
                        {
                            "id": tc.get("id", ""),
                            "type": "function",
                            "function": {
                                "name": tc.get("name", ""),
                                "arguments": json.dumps(tc.get("input", {})),
                            }
                        }
                        for tc in tool_calls
                    ]
                openai_messages.append(msg_dict)

    while not budget.exhausted:
        budget.iterations += 1
        logger.info(
            "Iteration %d/%d [openai]",
            budget.iterations,
            budget.max_iterations,
        )

        # Call LLM
        try:
            response = client.chat.completions.create(
                model=model,
                max_tokens=max_tokens,
                messages=openai_messages,
                tools=openai_tools,
            )
        except Exception as e:
            logger.error("API error: %s", e)
            return RunResult(
                status="error",
                summary=last_text,
                budget=budget.summary,
                messages=openai_messages,
                error=str(e),
            )

        # Track tokens (if available)
        if response.usage:
            budget.input_tokens += response.usage.prompt_tokens
            budget.output_tokens += response.usage.completion_tokens

        # Process response
        choice = response.choices[0]
        assistant_msg = choice.message

        # Get text content
        if assistant_msg.content:
            last_text = assistant_msg.content
            logger.info("Assistant: %s", last_text[:200])

        # Append assistant message
        openai_messages.append(assistant_msg.to_dict())

        # Check for tool calls
        tool_calls = assistant_msg.tool_calls or []

        # If no tool calls, agent is done
        if not tool_calls:
            return RunResult(
                status="completed",
                summary=last_text,
                budget=budget.summary,
                messages=openai_messages,
            )

        # Execute tool calls
        for tc in tool_calls:
            logger.info("Tool call: %s(%s)", tc.function.name, tc.function.arguments[:200])

            # Parse arguments
            try:
                args = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                args = {}

            result = tools.call(tc.function.name, args)

            # Serialize result
            if isinstance(result, str):
                result_str = result
            else:
                result_str = json.dumps(result, ensure_ascii=False, indent=2)

            logger.info("Tool result: %s", result_str[:200])

            openai_messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result_str,
            })

    # Budget exhausted
    return RunResult(
        status="budget_exhausted",
        summary=last_text,
        budget=budget.summary,
        messages=openai_messages,
    )