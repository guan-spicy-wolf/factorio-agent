"""Agent entry point: wire up tools, load config, run the loop.

Usage:
    uv run python -m agent.run "在空地图上放置一个采矿机对准铁矿"
    uv run python -m agent.run --provider openai --base-url http://localhost:8000/v1 "测试任务"
    uv run python -m agent.run --no-server "测试 API 文档搜索功能"
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path

# Load .env file if exists
def load_env(env_path: str = "./.env") -> None:
    """Load environment variables from .env file."""
    env_file = Path(env_path)
    if not env_file.exists():
        return
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip()
                # Don't override existing env vars
                if key not in os.environ:
                    os.environ[key] = value

load_env()

from agent.api_docs import ApiIndex
from agent.loop import run, DEFAULT_PROVIDER, DEFAULT_MODEL_ANTHROPIC, DEFAULT_MODEL_OPENAI, DEFAULT_MAX_ITERATIONS
from agent.memory import init_memory, memory_read, memory_append, memory_write
from agent.scripts import list_scripts, read_script, write_script, get_script_template
from agent.tools import ToolRegistry, tool
from agent.review import get_review_manager

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
- **Item acquisition**: Use `give_item(name, count)` to add items to inventory.

## Workflow Pattern

1. **Spawn**: Call `spawn()` or `spawn({items})` to create your character
2. **Explore**: Use `inspect()` to see what's around you
3. **Move**: Get close to target with `move(x, y)`
4. **Check**: Use `check_item(name, count)` or `inventory()` to verify items
5. **Act**: `place(name, x, y)` or `remove(x, y)` to interact with world

## Available Tools

- **spawn(items)**: Create your character. Optional items dict. Do this FIRST.
- **move(x, y)**: Teleport to position. Must be near target to place/remove.
- **inventory()**: List all items in your inventory.
- **check_item(name, count)**: Check if you have enough of an item.
- **give_item(name, count)**: Add items to inventory (cheat mode).
- **inspect(x, y, radius)**: See entities and resources in an area. \
If no position given, shows area around your character.
- **place(name, x, y, direction)**: Place entity from inventory. \
Requires item in inventory AND being in range. direction: 0=N, 1=E, 2=S, 3=W.
- **remove(x, y, name?)**: Remove entity, get item back. Must be in range.
- **api_search(query)**: Search Factorio Lua API documentation.
- **api_detail(name)**: Get detailed info about an API entry.
- **script_list()**: List all available Lua scripts.
- **script_read(name)**: Read a script's source code.
- **script_write(name, code)**: Write a new script (extends your capabilities!).
- **script_template(category, name)**: Get a template for new script.
- **memory_read/write/append**: Persistent memory across sessions.

## Script Evolution

You can **extend your own capabilities** by writing new Lua scripts:

1. Use `script_list()` to see existing scripts
2. Use `script_read("atomic.teleport")` to learn the pattern
3. Use `script_template("atomic", "my_action")` for a starter template
4. Write your script with `script_write("atomic.my_action", code)`
5. Writing a script only updates the repo copy. The running Factorio mod still
   needs a save reload or server restart before the new script becomes callable.

Script categories:
- **atomic/**: Single API call (teleport, inventory_get, etc.)
- **actions/**: Multi-step workflows (spawn, place, etc.)
- **examples/**: Demonstration scripts for learning

## Example Task Flow

Task: "Place a mining drill on iron ore"
1. spawn({"electric-mining-drill": 5})  # Create character with items
2. inspect(radius=30)                    # Find iron ore nearby
3. move(x=ore_x, y=ore_y - 5)            # Move close to ore
4. place("electric-mining-drill", ore_x, ore_y, 0)  # Place it
5. inspect(radius=5)                     # Verify placement

## Guidelines

1. ALWAYS spawn first - without a character, you cannot interact with the world.
2. Check inventory before placing - use `give_item()` if missing items.
3. Move before acting - you must be close enough to place/remove.
4. Be methodical: inspect → plan → move → act → verify.
5. Save important learnings to memory.

## Script Evolution (Advanced)

You can create new scripts to extend your capabilities:
- **script_list()**: See all available scripts (atomic, actions, examples).
- **script_read(name)**: Read existing script source code to learn patterns.
- **script_write(name, code)**: Write a new script - immediately usable!
- **script_template(category, name)**: Get a template for a new script.
- **script_reload(name?)**: Force reload script(s) if needed.

Scripts are auto-approved and hot-reloaded, so new scripts work immediately.
"""


def build_tools(
    bridge=None,
    api_index: ApiIndex | None = None,
) -> ToolRegistry:
    """Build the tool registry with all available tools."""
    registry = ToolRegistry()
    review_manager = get_review_manager()

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

    # Script management tools (for evolution)
    @tool
    def script_list() -> dict:
        """List all available Lua scripts in the mod.

        Returns scripts organized by category: atomic, actions, examples.
        Use this to discover what capabilities the agent has.
        """
        return list_scripts()

    @tool
    def script_read(name: str) -> dict:
        """Read a Lua script's source code.

        Args:
            name: Script name in format 'category.name' (e.g. 'atomic.teleport', 'actions.spawn')

        Returns the script's code, path, and line count.
        Use this to learn how existing scripts work.
        """
        return read_script(name)

    @tool
    def script_write(name: str, code: str, description: str = "") -> dict:
        """Write a new Lua script or update an existing one.

        Args:
            name: Script name in format 'category.name' (e.g. 'atomic.my_action', 'actions.my_workflow')
            code: Lua source code
            description: Optional description for the script

        The script will be saved to mod/scripts/{category}/{name}.lua.
        The running Factorio mod still needs a save reload or restart before
        the new or updated script becomes callable.

        IMPORTANT: Scripts must return a function that takes args_str and returns JSON.
        Use script_template() to get a starting template.
        """
        return write_script(name, code, description)

    @tool
    def script_template(category: str, name: str) -> str:
        """Get a template for creating a new Lua script.

        Args:
            category: One of 'atomic', 'actions', 'examples', 'lib'
            name: Script name (without .lua extension)

        Returns a starter template with proper structure.
        """
        return get_script_template(category, name)

    registry.register(api_search)
    registry.register(api_detail)
    registry.register(script_list)
    registry.register(script_read)
    registry.register(script_write)
    registry.register(script_template)

    # Memory tools
    registry.register(memory_read)
    registry.register(memory_append)
    registry.register(memory_write)

    # Script management tools
    from agent.scripts import list_scripts, read_script, write_script, get_script_template

    @tool
    def script_list() -> dict:
        """List all available Lua scripts.

        Returns dict with categories: atomic, actions, examples, lib.
        Use script_read to see source code, script_template for starters.
        """
        return list_scripts()

    @tool
    def script_read(name: str) -> dict:
        """Read a Lua script's source code.

        Args:
            name: Script name like 'atomic.teleport' or 'actions.spawn'

        Returns code, path, lines. Use to learn existing patterns.
        """
        return read_script(name)

    @tool
    def script_write(name: str, code: str, description: str = "") -> dict:
        """Write a new Lua script or update an existing one.

        The script will be saved and immediately activated (no restart needed).
        Use script_read to learn existing patterns and script_template for starters.

        Args:
            name: Script name in format 'category.name' (e.g. 'atomic.my_action', 'actions.my_workflow')
            code: Lua source code
            description: Optional description for the script

        Returns:
            {"created": true, "activation": "immediate"} if successful
        """
        # Submit for review (current: auto-approve)
        review_result = review_manager.submit(name, code)

        if review_result["status"] != "approved":
            return {
                "status": "pending_review",
                "name": name,
                "pr_url": review_result.get("pr_url"),
            }

        # Write with register callback (hot-reload via code string)
        def register_callback(script_name: str) -> dict:
            if bridge is None:
                return {"ok": False, "error": "no server connection"}
            return bridge.register_script(script_name, code)

        return write_script(name, code, description, reload_callback=register_callback)

    @tool
    def script_template(category: str, name: str) -> str:
        """Get a template for a new script.

        Args:
            category: 'atomic', 'actions', 'examples', or 'lib'
            name: Script name (without category prefix)

        Returns template Lua code with placeholders.
        """
        return get_script_template(category, name)

    @tool
    def script_reload(name: str = "") -> dict:
        """Reload a script or all scripts.

        Usually called automatically after script_write.
        Use this if you suspect the script cache is stale.

        Args:
            name: Script name to reload, or empty to reload all scripts.

        Returns:
            {"ok": true, "reloaded": name_or_all}
        """
        if bridge is None:
            return {"error": "no server connection"}

        if name:
            return bridge.reload_script(name)
        else:
            return bridge.reload_all()

    registry.register(script_list)
    registry.register(script_read)
    registry.register(script_write)
    registry.register(script_template)
    registry.register(script_reload)

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
        def remove(x: float, y: float, name: str = "") -> dict:
            """Remove an entity at target position.

            REQUIRES: Agent within range (~10 tiles).
            Item is returned to inventory.
            """
            return bridge.remove(x, y, name if name else None)

        @tool
        def give_item(name: str, count: int = 1) -> dict:
            """Add items to agent's inventory (cheat mode).

            Args:
                name: Item name (e.g. "stone-furnace", "iron-plate").
                count: Number of items to add.

            Use this when you need items that you don't have.
            """
            return bridge.atomic_inventory_add(name, count)

        # Register all tools
        for fn in [spawn, move, inventory, check_item, inspect, place, remove, give_item]:
            registry.register(fn)

    return registry


def main():
    parser = argparse.ArgumentParser(description="Run the Factorio agent")
    parser.add_argument("task", help="Task description for the agent")
    parser.add_argument(
        "--provider",
        choices=["anthropic", "openai"],
        default=os.environ.get("LLM_PROVIDER", DEFAULT_PROVIDER),
        help="LLM provider (anthropic or openai)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Model ID (default based on provider)",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("OPENAI_BASE_URL", None),
        help="OpenAI-compatible base URL",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="API key (defaults to env: ANTHROPIC_API_KEY or OPENAI_API_KEY)",
    )
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

    # Set default model based on provider
    model = args.model
    if model is None:
        model = DEFAULT_MODEL_ANTHROPIC if args.provider == "anthropic" else DEFAULT_MODEL_OPENAI

    # Initialize memory
    init_memory(args.memory_path)

    # Build tools
    bridge = None
    if not args.no_server:
        from agent.bridge import FactorioBridge
        from agent.rcon import RCONClient

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
        provider=args.provider,
        model=model,
        max_iterations=args.max_iterations,
        memory_context=memory_content if memory_content != "(memory is empty)" else None,
        api_key=args.api_key,
        base_url=args.base_url,
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
