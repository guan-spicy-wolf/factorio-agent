"""High-level bridge between Python agent and Factorio mod scripts."""

import json
import logging
from typing import Optional

from agent.rcon import RCONClient

logger = logging.getLogger(__name__)


class ScriptError(Exception):
    """A Lua script returned an error."""


class FactorioBridge:
    """Executes agent scripts in Factorio via RCON.

    Script hierarchy:
      atomic.*  - Raw API calls (teleport, inventory_*, cursor_*, build_from_cursor, mine_entity)
      actions.* - Common workflows (spawn, move, place, remove, inspect, inventory)
      examples.*- Example scripts (build_belt_line, setup_mining)

    Actions auto-advance time. Atomics do not.
    """

    # Scripts that trigger auto-advance after execution
    AUTO_ADVANCE_SCRIPTS = {
        "actions.spawn": 10,
        "actions.move": 10,
        "actions.place": 60,
        "actions.remove": 10,
        # Legacy shortcuts
        "spawn": 10,
        "move": 10,
        "place": 60,
        "remove": 10,
    }

    def __init__(self, rcon: RCONClient):
        self.rcon = rcon
        self.call_count = 0
        self.auto_advance = True
        self._last_tick = 0

    def call_script(self, name: str, args: str = "") -> dict:
        """Execute a script and return parsed result.

        Args:
            name: Script name (e.g. "actions.place", "atomic.teleport", "examples.build_belt_line")
            args: Arguments string (JSON or plain)
        """
        command = f"/agent {name} {args}".strip()
        logger.debug("call_script: %s", command)

        raw = self.rcon.send_command(command)
        self.call_count += 1

        logger.debug("response: %s", raw[:200])

        result = json.loads(raw)
        if isinstance(result, dict) and "error" in result:
            raise ScriptError(result["error"])

        # Auto-advance time for action scripts
        if self.auto_advance and name in self.AUTO_ADVANCE_SCRIPTS:
            ticks = self.AUTO_ADVANCE_SCRIPTS[name]
            self._advance(ticks)
            if isinstance(result, dict):
                result["tick"] = self._last_tick

        return result

    def _raw_call(self, name: str, args: str = "") -> dict:
        """Execute script without auto-advance."""
        command = f"/agent {name} {args}".strip()
        raw = self.rcon.send_command(command)
        self.call_count += 1
        result = json.loads(raw)
        if isinstance(result, dict) and "error" in result:
            raise ScriptError(result["error"])
        return result

    def _advance(self, ticks: int) -> dict:
        """Internal: advance game time."""
        result = self._raw_call("advance", str(ticks))
        self._last_tick = result.get("tick_before", 0) + ticks
        return result

    def ping(self) -> dict:
        """Verify connectivity."""
        return self._raw_call("ping")

    # ==================== Atomic Operations ====================

    def atomic_teleport(self, x: float, y: float) -> dict:
        """Teleport to position (instant)."""
        return self._raw_call("atomic.teleport", json.dumps({"x": x, "y": y}))

    def atomic_inventory_get(self) -> dict:
        """Get inventory contents."""
        return self._raw_call("atomic.inventory_get")

    def atomic_inventory_add(self, name: str, count: int = 1) -> dict:
        """Add items to inventory."""
        return self._raw_call("atomic.inventory_add", json.dumps({"name": name, "count": count}))

    def atomic_inventory_remove(self, name: str, count: int = 1) -> dict:
        """Remove items from inventory."""
        return self._raw_call("atomic.inventory_remove", json.dumps({"name": name, "count": count}))

    def atomic_inventory_count(self, name: str) -> dict:
        """Count items in inventory."""
        return self._raw_call("atomic.inventory_count", json.dumps({"name": name}))

    def atomic_cursor_set(self, name: str) -> dict:
        """Set cursor to item."""
        return self._raw_call("atomic.cursor_set", json.dumps({"name": name}))

    def atomic_cursor_clear(self) -> dict:
        """Clear cursor."""
        return self._raw_call("atomic.cursor_clear")

    def atomic_cursor_get(self) -> dict:
        """Get cursor contents."""
        return self._raw_call("atomic.cursor_get")

    def atomic_build_from_cursor(self, x: float, y: float, direction: int = 0) -> dict:
        """Build entity from cursor."""
        return self._raw_call("atomic.build_from_cursor", json.dumps({"x": x, "y": y, "direction": direction}))

    def atomic_mine_entity(self, x: float, y: float, name: Optional[str] = None) -> dict:
        """Mine entity at position."""
        args = {"x": x, "y": y}
        if name:
            args["name"] = name
        return self._raw_call("atomic.mine_entity", json.dumps(args))

    def atomic_can_reach(self, x: float, y: float) -> dict:
        """Check if can reach position."""
        return self._raw_call("atomic.can_reach", json.dumps({"x": x, "y": y}))

    def atomic_can_place(self, name: str, x: float, y: float, direction: int = 0) -> dict:
        """Check if can place entity."""
        return self._raw_call("atomic.can_place", json.dumps({"name": name, "x": x, "y": y, "direction": direction}))

    # ==================== Action Scripts ====================

    def spawn(self, items: Optional[dict] = None) -> dict:
        """Spawn character with items."""
        if items:
            return self.call_script("spawn", json.dumps({"items": items}))
        return self.call_script("spawn")

    def move(self, x: float, y: float) -> dict:
        """Move to position."""
        return self.call_script("move", json.dumps({"x": x, "y": y}))

    def inventory(self) -> dict:
        """Query inventory."""
        return self.call_script("inventory")

    def check_item(self, name: str) -> dict:
        """Check item count."""
        return self.call_script("inventory", json.dumps({"check": name}))

    def inspect(self, x: float = 0, y: float = 0, radius: float = 10) -> dict:
        """Inspect area."""
        return self.call_script("inspect", json.dumps({"x": x, "y": y, "radius": radius}))

    def place(self, name: str, x: float, y: float, direction: int = 0) -> dict:
        """Place entity (real flow: inventory → cursor → build)."""
        return self.call_script("place", json.dumps({"name": name, "x": x, "y": y, "direction": direction}))

    def remove(self, x: float, y: float, name: Optional[str] = None) -> dict:
        """Remove entity (real flow: mine → inventory)."""
        args = {"x": x, "y": y}
        if name:
            args["name"] = name
        return self.call_script("remove", json.dumps(args))

    def wait(self, ticks: int = 60) -> dict:
        """Wait (advance time)."""
        return self._advance(ticks)

    # ==================== Example Scripts ====================

    def example_build_belt_line(self, start_x: float, start_y: float, length: int, direction: int = 1) -> dict:
        """Build a line of belts (example script)."""
        return self.call_script("examples.build_belt_line", json.dumps({
            "start_x": start_x, "start_y": start_y, "length": length, "direction": direction
        }))

    def example_setup_mining(self, ore_x: float, ore_y: float) -> dict:
        """Set up mining station (example script)."""
        return self.call_script("examples.setup_mining", json.dumps({
            "ore_x": ore_x, "ore_y": ore_y
        }))

    # ==================== Script Management ====================

    def reload_script(self, name: str) -> dict:
        """Reload a specific script to pick up changes.

        Args:
            name: Script name (e.g. "atomic.my_action")

        Returns:
            {"ok": true, "reloaded": name}
        """
        return self._raw_call("reload", name)

    def reload_all(self) -> dict:
        """Reload all scripts.

        Clears the script cache, next call will load fresh.

        Returns:
            {"ok": true, "reloaded": "all"}
        """
        return self._raw_call("reload_all")