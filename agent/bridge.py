"""High-level bridge between Python agent and Factorio mod scripts."""

import json
import logging

from agent.rcon import RCONClient

logger = logging.getLogger(__name__)


class ScriptError(Exception):
    """A Lua script returned an error."""


class FactorioBridge:
    """Executes agent scripts in Factorio via RCON.

    All interaction with the game world goes through call_script(),
    which sends /agent <name> <args> and parses the JSON response.
    """

    def __init__(self, rcon: RCONClient):
        self.rcon = rcon
        self.call_count = 0

    def call_script(self, name: str, args: str = "") -> dict:
        """Execute a script in the Factorio mod and return parsed result.

        Args:
            name: Script name (e.g. "inspect", "place", "advance").
            args: Arguments string passed to the Lua script.

        Returns:
            Parsed JSON dict from the script's return value.

        Raises:
            ScriptError: If the script returned an error.
            json.JSONDecodeError: If the response isn't valid JSON.
        """
        command = f"/agent {name} {args}".strip()
        logger.debug("call_script: %s", command)

        raw = self.rcon.send_command(command)
        self.call_count += 1

        logger.debug("response: %s", raw[:200])

        result = json.loads(raw)
        if isinstance(result, dict) and "error" in result:
            raise ScriptError(result["error"])

        return result

    def ping(self) -> dict:
        """Verify connectivity with a ping script call."""
        return self.call_script("ping")

    def inspect(self, x: float = 0, y: float = 0, radius: float = 10) -> dict:
        """Query entities in an area."""
        args = json.dumps({"x": x, "y": y, "radius": radius})
        return self.call_script("inspect", args)

    def advance(self, ticks: int = 60) -> dict:
        """Advance game time by N ticks."""
        return self.call_script("advance", str(ticks))
