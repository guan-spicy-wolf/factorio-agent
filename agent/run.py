"""Agent entry point: wire up tools, load config, run the loop.

Usage:
    uv run python -m agent.run "在空地图上放置一个采矿机对准铁矿"
    uv run python -m agent.run --no-server "测试 API 文档搜索功能"
"""

import argparse
import json
import logging
import sys
from pathlib import Path

from agent.api_docs import ApiIndex
from agent.loop import run, DEFAULT_MODEL, DEFAULT_MAX_ITERATIONS
from agent.memory import init_memory, memory_read, memory_append, memory_write
from agent.tools import ToolRegistry, tool

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a Factorio automation agent. You operate a Factorio headless server \
through pre-approved Lua scripts, and you can search Factorio API documentation \
to learn how to write new scripts.

## Available tools

- **call_script(name, args)**: Execute a Lua script in the Factorio mod via RCON. \
Scripts available: inspect, place, remove, advance, ping. \
Args are passed as a string (plain value or JSON).
- **api_search(query)**: Search Factorio Lua API docs. Returns matching classes, \
methods, attributes, events, prototypes.
- **api_detail(name)**: Get full details for a specific API entry (parameters, \
return values, description, examples).
- **memory_read()**: Read your persistent memory file.
- **memory_write(content)**: Overwrite your memory with new content.
- **memory_append(note)**: Append a note to memory for future reference.

## Guidelines

1. Start by reading your memory to recall prior knowledge.
2. Use inspect to understand the current game state before acting.
3. Use api_search/api_detail to look up API details when writing or planning scripts.
4. Use advance to progress game time after placing entities.
5. Save important observations and learned patterns to memory.
6. Be methodical: inspect → plan → act → verify → record.
"""


def build_tools(
    bridge=None,
    api_index: ApiIndex | None = None,
) -> ToolRegistry:
    """Build the tool registry with all available tools."""
    registry = ToolRegistry()

    # API documentation tools
    if api_index is None:
        api_index = ApiIndex()
        api_index.load()

    @tool
    def api_search(query: str, limit: int = 20) -> list[dict]:
        """Search Factorio API docs by keyword.

        Returns matching classes, methods, attributes, events, prototypes, etc.
        """
        return api_index.search(query, limit=limit)

    @tool
    def api_detail(name: str) -> dict:
        """Get full details for a Factorio API entry.

        Provide the qualified name (e.g. 'LuaSurface.create_entity',
        'TransportBeltPrototype', 'defines.direction').
        Returns parameters, return values, description, examples.
        """
        result = api_index.detail(name)
        if result is None:
            return {"error": f"not found: {name}"}
        return result

    registry.register(api_search)
    registry.register(api_detail)

    # Memory tools
    registry.register(memory_read)
    registry.register(memory_append)
    registry.register(memory_write)

    # Factorio bridge tools (only if server is available)
    if bridge is not None:

        @tool
        def call_script(name: str, args: str = "") -> dict:
            """Execute a Lua script in the Factorio mod via RCON.

            Available scripts: ping, inspect, advance, place, remove.
            Args are passed as a string — either a plain value or JSON.
            """
            return bridge.call_script(name, args)

        registry.register(call_script)

    return registry


def main():
    parser = argparse.ArgumentParser(description="Run the Factorio agent")
    parser.add_argument("task", help="Task description for the agent")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Model ID")
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=DEFAULT_MAX_ITERATIONS,
        help="Max LLM round-trips",
    )
    parser.add_argument(
        "--no-server",
        action="store_true",
        help="Run without Factorio server (API docs + memory only)",
    )
    parser.add_argument(
        "--memory-path",
        default="memory/agent_notes.md",
        help="Path to memory file",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    # Initialize memory
    init_memory(args.memory_path)

    # Build tools
    bridge = None
    if not args.no_server:
        from agent.bridge import FactorioBridge
        from agent.rcon import RCONClient
        import os

        rcon = RCONClient(
            host=os.environ.get("RCON_HOST", "127.0.0.1"),
            port=int(os.environ.get("RCON_PORT", "27015")),
            password=os.environ.get("RCON_PASSWORD", "changeme"),
        )
        rcon.connect()
        bridge = FactorioBridge(rcon)
        logger.info("Connected to Factorio server")

    tools = build_tools(bridge=bridge)
    logger.info("Registered tools: %s", tools.names)

    # Read memory for context
    memory_content = memory_read()

    # Run
    result = run(
        task=args.task,
        system_prompt=SYSTEM_PROMPT,
        tools=tools,
        model=args.model,
        max_iterations=args.max_iterations,
        memory_context=memory_content if memory_content != "(memory is empty)" else None,
    )

    # Output
    print(f"\n{'='*60}")
    print(f"Status: {result.status}")
    print(f"Budget: {json.dumps(result.budget, indent=2)}")
    if result.error:
        print(f"Error: {result.error}")
    print(f"\nSummary:\n{result.summary}")


if __name__ == "__main__":
    main()
