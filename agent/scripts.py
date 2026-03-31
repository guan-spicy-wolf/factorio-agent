"""Script management: read and write Lua scripts inside the mod tree."""

from pathlib import Path
from typing import Callable, Optional

# Project root (where this file's parent is)
PROJECT_ROOT = Path(__file__).parent.parent
MOD_SCRIPTS_DIR = PROJECT_ROOT / "mod" / "scripts"


def list_scripts() -> dict:
    """List all available Lua scripts in the mod.

    Returns:
        {
            "atomic": ["teleport", "inventory_get", ...],
            "actions": ["spawn", "move", ...],
            "examples": ["build_belt_line", ...],
            "lib": ["serialize", "agent", ...]
        }
    """
    result = {}

    for category in ["atomic", "actions", "examples", "lib"]:
        category_dir = MOD_SCRIPTS_DIR / category
        if category_dir.exists():
            scripts = []
            for f in category_dir.glob("*.lua"):
                scripts.append(f.stem)  # filename without .lua
            result[category] = sorted(scripts)
        else:
            result[category] = []

    return result


def read_script(name: str) -> dict:
    """Read a Lua script's source code.

    Args:
        name: Script name (e.g. "atomic.teleport", "actions.spawn", "lib.serialize")

    Returns:
        {"path": "...", "code": "..."} or {"error": "..."}
    """
    # Parse category.script format
    parts = name.split(".")
    if len(parts) == 2:
        category, script_name = parts
    else:
        return {"error": f"invalid script name format: {name}. Use 'category.script' like 'atomic.teleport'"}

    script_path = MOD_SCRIPTS_DIR / category / f"{script_name}.lua"

    if not script_path.exists():
        return {"error": f"script not found: {name}"}

    try:
        code = script_path.read_text(encoding="utf-8")
        # Build relative path safely
        try:
            rel_path = str(script_path.relative_to(PROJECT_ROOT))
        except ValueError:
            rel_path = str(script_path)
        return {
            "name": name,
            "path": rel_path,
            "code": code,
            "lines": len(code.splitlines()),
        }
    except Exception as e:
        return {"error": f"failed to read script: {e}"}


def write_script(
    name: str,
    code: str,
    description: str = "",
    reload_callback: Optional[Callable[[str], dict]] = None,
) -> dict:
    """Write a new Lua script or update an existing one.

    The script will be written to mod/scripts/{category}/{name}.lua.
    If reload_callback is provided, the script becomes immediately usable
    without restarting Factorio.

    Args:
        name: Script name (e.g. "atomic.new_action", "actions.my_workflow")
        code: Lua source code
        description: Optional description for commit message
        reload_callback: Optional callback to reload script after write.
                         Called as reload_callback(name) -> dict.
                         If provided and succeeds, activation is "immediate".

    Returns:
        {"created": true, "path": "...", "activation": "immediate"}
        or {"activation": "restart_required"} if no reload_callback.
        or {"activation": "reload_failed"} if reload failed.
    """
    # Parse category.script format
    parts = name.split(".")
    if len(parts) != 2:
        return {"error": f"invalid script name format: {name}. Use 'category.script' like 'atomic.teleport'"}

    category, script_name = parts

    # Validate category
    valid_categories = ["atomic", "actions", "examples", "lib"]
    if category not in valid_categories:
        return {"error": f"invalid category: {category}. Must be one of: {valid_categories}"}

    # Validate script name (alphanumeric and underscore only)
    if not script_name.replace("_", "").isalnum():
        return {"error": f"invalid script name: {script_name}. Use only letters, numbers, and underscores"}

    # Create directory if needed
    script_dir = MOD_SCRIPTS_DIR / category
    script_dir.mkdir(parents=True, exist_ok=True)

    script_path = script_dir / f"{script_name}.lua"

    # Check if this is a new script or update
    is_new = not script_path.exists()

    try:
        script_path.write_text(code, encoding="utf-8")
    except Exception as e:
        return {"error": f"failed to write script: {e}"}

    # Determine activation status
    activation = "restart_required"
    if reload_callback:
        try:
            reload_result = reload_callback(name)
            if reload_result.get("ok") or reload_result.get("reloaded"):
                activation = "immediate"
            else:
                activation = "reload_failed"
        except Exception:
            activation = "reload_failed"

    # Build relative path safely
    try:
        rel_path = str(script_path.relative_to(PROJECT_ROOT))
    except ValueError:
        # If not under PROJECT_ROOT (e.g., in tests), use absolute path
        rel_path = str(script_path)

    return {
        "created": is_new,
        "updated": not is_new,
        "name": name,
        "path": rel_path,
        "description": description,
        "lines": len(code.splitlines()),
        "activation": activation,
    }


def delete_script(name: str) -> dict:
    """Delete a Lua script.

    Args:
        name: Script name (e.g. "atomic.old_action")

    Returns:
        {"deleted": true} or {"error": "..."}
    """
    parts = name.split(".")
    if len(parts) != 2:
        return {"error": f"invalid script name format: {name}"}

    category, script_name = parts
    script_path = MOD_SCRIPTS_DIR / category / f"{script_name}.lua"

    if not script_path.exists():
        return {"error": f"script not found: {name}"}

    try:
        script_path.unlink()
        return {"deleted": True, "name": name}
    except Exception as e:
        return {"error": f"failed to delete script: {e}"}


def get_script_template(category: str, name: str) -> str:
    """Get a template for a new script.

    Args:
        category: "atomic", "actions", "examples", or "lib"
        name: Script name

    Returns:
        Template Lua code
    """
    templates = {
        "atomic": f'''-- atomic/{name}.lua
-- Description of what this atomic operation does.
-- Args: {{"param1": "value1"}}
-- Returns: {{"result": "..."}}

local serialize = require("scripts.lib.serialize")
local agent = require("scripts.lib.agent")

return function(args_str)
    local e = agent.get()
    if not e then
        return serialize({{error = "agent not spawned"}})
    end

    -- Parse args
    -- local param1 = args_str:match('"param1"%s*:%s*"([^"]+)"')

    -- Do something

    return serialize({{ok = true}})
end
''',
        "actions": f'''-- actions/{name}.lua
-- Action: Description of this action workflow.
-- Flow: step1 -> step2 -> step3
-- Args: {{"param1": "value1"}}
-- Returns: {{"result": "..."}}

local serialize = require("scripts.lib.serialize")
local agent = require("scripts.lib.agent")

return function(args_str)
    local e = agent.get()
    if not e then
        return serialize({{error = "agent not spawned"}})
    end

    -- Parse args and execute workflow

    return serialize({{ok = true}})
end
''',
        "examples": f'''-- examples/{name}.lua
-- Example: Description of what this example demonstrates.
-- This script shows how to use atomic operations to accomplish a task.
-- Args: {{"param1": "value1"}}
-- Returns: {{"result": "..."}}

local serialize = require("scripts.lib.serialize")
local agent = require("scripts.lib.agent")

return function(args_str)
    local e = agent.get()
    if not e then
        return serialize({{error = "agent not spawned"}})
    end

    -- Example workflow

    return serialize({{ok = true}})
end
''',
        "lib": f'''-- lib/{name}.lua
-- Library: Description of this utility library.
-- Provides helper functions for other scripts.

local M = {{}}

-- function M.helper(...)
--     ...
-- end

return M
''',
    }

    return templates.get(category, templates["atomic"])