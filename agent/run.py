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
You are a Factorio automation agent. You control a character in the game world \
through pre-approved Lua scripts, and you can search Factorio API documentation \
to learn how to write new scripts.

## IMPORTANT: Character Constraints

You operate through a **character** that must be spawned first. The character has:
- **Inventory**: Items must be in inventory to place entities. Check with `inventory()`.
- **Reach distance**: You must be within ~10 tiles of target to place/remove. Use `move(x, y)` first.
- **Position**: Use `inspect()` to see entities around you.

## Workflow Pattern

1. **Spawn**: Call `spawn()` or `spawn({items})` to create your character
2. **Explore**: Use `inspect()` to see what's around you
3. **Move**: Get close to target with `move(x, y)`
4. **Check**: Use `check_item(name, count)` or `inventory()` to verify items
5. **Act**: `place(name, x, y)` or `remove(x, y)` to interact with world
6. **Wait**: Use `wait(ticks)` to let production run (60 ticks = 1 second)

## Available Tools

- **spawn(items)**: Create your character. Optional items dict. Do this FIRST.
- **move(x, y)**: Teleport to position. Must be near target to place/remove.
- **inventory()**: List all items in your inventory.
- **check_item(name, count)**: Check if you have enough of an item.
- **inspect(x, y, radius)**: See entities and resources in an area. \
If no position given, shows area around your character.
- **place(name, x, y, direction)**: Place entity from inventory. \
Requires item in inventory AND being in range. direction: 0=N, 1=E, 2=S, 3=W.
- **remove(x, y, name?)**: Remove entity, get item back. Must be in range.
- **wait(ticks)**: Advance game time to see production progress.
- **api_search(query)**: Search Factorio Lua API documentation.
- **api_detail(name)**: Get detailed info about an API entry.
- **memory_read/write/append**: Persistent memory across sessions.

## Example Task Flow

Task: "Place a mining drill on iron ore"
1. spawn({"electric-mining-drill": 5})  # Create character with items
2. inspect(radius=30)                    # Find iron ore nearby
3. move(x=ore_x, y=ore_y - 5)            # Move close to ore
4. place("electric-mining-drill", ore_x, ore_y, 0)  # Place it
5. wait(60)                              # Let it run a bit
6. inspect(radius=5)                     # Verify placement

## Guidelines

1. ALWAYS spawn first - without a character, you cannot interact with the world.
2. Check inventory before placing - you need items to build.
3. Move before acting - you must be close enough to place/remove.
4. Be methodical: inspect → plan → move → act → verify.
5. Save important learnings to memory.
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
        def spawn(items: dict | None = None) -> dict:
            """Spawn the agent character with optional starting items.

            Args:
                items: Optional dict of {item_name: count} to give to agent.
                       Example: {"iron-plate": 100, "electric-mining-drill": 5}

            MUST be called first before other world interactions.
            """
            return bridge.spawn(items)

        @tool
        def move(x: float, y: float) -> dict:
            """Move agent to target position.

            Must be within ~10 tiles of target to place/remove entities.
            """
            return bridge.move(x, y)

        @tool
        def inventory() -> dict:
            """Query agent's inventory. Returns list of items with counts."""
            return bridge.inventory()

        @tool
        def check_item(name: str, count: int = 1) -> dict:
            """Check if agent has enough of an item in inventory.

            Returns has_enough, have, need, and missing count.
            """
            return bridge.check_item(name, count)

        @tool
        def inspect(x: float = 0, y: float = 0, radius: float = 10) -> dict:
            """Query entities and resources in an area.

            If agent is spawned, defaults to area around agent position.
            Returns entities, resources, and their positions.
            """
            return bridge.inspect(x, y, radius)

        @tool
        def place(name: str, x: float, y: float, direction: int = 0) -> dict:
            """Place an entity at target position.

            Args:
                name: Entity name (e.g. "electric-mining-drill", "iron-chest").
                x, y: Target position.
                direction: 0=north, 1=east, 2=south, 3=west.

            REQUIRES: Item in inventory AND agent within range (~10 tiles).
            """
            return bridge.place(name, x, y, direction)

        @tool
        def remove(x: float, y: float, name: str = "", radius: float = 1) -> dict:
            """Remove an entity at target position.

            REQUIRES: Agent within range (~10 tiles).
            Item is returned to inventory.
            """
            return bridge.remove(x, y, name if name else None, radius)

        @tool
        def wait(ticks: int = 60) -> dict:
            """Wait (advance time) without performing any action.

            Use to observe production progress, belt movement, etc.
            60 ticks = 1 second at normal game speed.
            """
            return bridge.wait(ticks)

        # Register all tools
        for fn in [spawn, move, inventory, check_item, inspect, place, remove, wait]:
            registry.register(fn)

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
