"""Agent main loop: system prompt → Anthropic API → tool dispatch → repeat.

Minimal implementation using the Anthropic Python SDK directly.
No streaming, no compaction, no multi-provider abstraction — just the loop.
"""

import json
import logging
import time
from dataclasses import dataclass, field

import anthropic

from agent.tools import ToolRegistry

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-sonnet-4-20250514"
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
    model: str = DEFAULT_MODEL,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    memory_context: str | None = None,
    api_key: str | None = None,
) -> RunResult:
    """Run the agent loop until completion or budget exhaustion.

    Args:
        task: The goal/task description for the agent.
        system_prompt: System prompt defining agent behavior.
        tools: Registry of callable tools.
        model: Anthropic model ID.
        max_iterations: Max LLM round-trips.
        max_tokens: Max tokens per LLM response.
        memory_context: Optional memory content to prepend to task.
        api_key: Anthropic API key (defaults to ANTHROPIC_API_KEY env).
    """
    client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
    budget = Budget(max_iterations=max_iterations)
    tool_schemas = tools.schemas()

    # Build initial messages
    messages: list[dict] = []

    # Include memory as initial context if available
    user_content = ""
    if memory_context and memory_context.strip():
        user_content += f"<memory>\n{memory_context}\n</memory>\n\n"
    user_content += f"<task>\n{task}\n</task>"

    messages.append({"role": "user", "content": user_content})

    last_text = ""

    while not budget.exhausted:
        budget.iterations += 1
        logger.info(
            "Iteration %d/%d (tokens: %d in, %d out)",
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
