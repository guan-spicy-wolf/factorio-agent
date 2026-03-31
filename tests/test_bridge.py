"""Unit tests for the FactorioBridge.

Tests JSON parsing and error handling with a mock RCON client.
"""

import json
import pytest

from agent.bridge import FactorioBridge, ScriptError
from agent.rcon import RCONClient


class MockRCON:
    """Mock RCON client that returns predefined responses."""

    def __init__(self, responses: dict[str, str] | None = None):
        self.responses = responses or {}
        self.commands_sent: list[str] = []

    def send_command(self, command: str) -> str:
        self.commands_sent.append(command)
        # Match by script name in the command
        for key, response in self.responses.items():
            if key in command:
                return response
        return '{"ok": true}'


class TestBridgeCallScript:
    """Test call_script JSON handling."""

    def test_successful_call(self):
        mock = MockRCON({"ping": '{"tick":0,"mod":"factorio-agent","status":"ok"}'})
        bridge = FactorioBridge(mock)  # type: ignore[arg-type]

        result = bridge.call_script("ping")

        assert result["tick"] == 0
        assert result["status"] == "ok"
        assert bridge.call_count == 1

    def test_script_error(self):
        mock = MockRCON({"bad": '{"error":"unknown script: bad"}'})
        bridge = FactorioBridge(mock)  # type: ignore[arg-type]

        with pytest.raises(ScriptError, match="unknown script"):
            bridge.call_script("bad")

    def test_invalid_json(self):
        mock = MockRCON({"broken": "not json"})
        bridge = FactorioBridge(mock)  # type: ignore[arg-type]

        with pytest.raises(json.JSONDecodeError):
            bridge.call_script("broken")

    def test_command_format(self):
        mock = MockRCON({"inspect": '{"entities":[],"count":0}'})
        bridge = FactorioBridge(mock)  # type: ignore[arg-type]

        bridge.call_script("inspect", '{"radius": 5}')

        assert len(mock.commands_sent) == 1
        assert mock.commands_sent[0] == '/agent inspect {"radius": 5}'

    def test_call_count_increments(self):
        mock = MockRCON({})
        bridge = FactorioBridge(mock)  # type: ignore[arg-type]

        bridge.call_script("a")
        bridge.call_script("b")
        bridge.call_script("c")

        assert bridge.call_count == 3


class TestBridgeHelpers:
    """Test convenience methods."""

    def test_ping(self):
        mock = MockRCON({"ping": '{"tick":100,"mod":"factorio-agent","status":"ok"}'})
        bridge = FactorioBridge(mock)  # type: ignore[arg-type]

        result = bridge.ping()
        assert result["tick"] == 100

    def test_inspect(self):
        mock = MockRCON({"inspect": '{"entities":[],"count":0,"total":0,"tick":0}'})
        bridge = FactorioBridge(mock)  # type: ignore[arg-type]

        result = bridge.inspect(x=5, y=10, radius=20)
        assert result["count"] == 0
        # Verify args contain coordinates
        assert '"x": 5' in mock.commands_sent[0]

    def test_wait(self):
        mock = MockRCON({"advance": '{"ticks_to_run":120,"tick_before":0}'})
        bridge = FactorioBridge(mock)  # type: ignore[arg-type]

        result = bridge.wait(ticks=120)
        assert result["ticks_to_run"] == 120
        assert "120" in mock.commands_sent[0]


class TestAutoAdvance:
    """Test automatic time advancement after action scripts."""

    def test_place_auto_advances(self):
        mock = MockRCON({
            "place": '{"placed":true,"entity":{"name":"chest"},"tick":0}',
            "advance": '{"ticks_to_run":60,"tick_before":0}',
        })
        bridge = FactorioBridge(mock)  # type: ignore[arg-type]

        result = bridge.call_script("place", '{"name":"chest"}')

        # Should have sent place + advance commands
        assert len(mock.commands_sent) == 2
        assert "place" in mock.commands_sent[0]
        assert "advance" in mock.commands_sent[1]
        # Tick should be updated to post-advance value
        assert result["tick"] == 60

    def test_remove_auto_advances(self):
        mock = MockRCON({
            "remove": '{"removed":true,"entity":{"name":"chest"},"tick":0}',
            "advance": '{"ticks_to_run":10,"tick_before":0}',
        })
        bridge = FactorioBridge(mock)  # type: ignore[arg-type]

        result = bridge.call_script("remove", '{"x":0}')

        assert len(mock.commands_sent) == 2
        assert "remove" in mock.commands_sent[0]
        assert "advance" in mock.commands_sent[1]
        assert result["tick"] == 10

    def test_inspect_no_auto_advance(self):
        mock = MockRCON({"inspect": '{"entities":[],"tick":0}'})
        bridge = FactorioBridge(mock)  # type: ignore[arg-type]

        result = bridge.call_script("inspect", '10')

        # Should only send inspect, no advance
        assert len(mock.commands_sent) == 1
        assert "inspect" in mock.commands_sent[0]

    def test_auto_advance_disabled(self):
        mock = MockRCON({
            "place": '{"placed":true,"entity":{"name":"chest"},"tick":0}',
            "advance": '{"ticks_to_run":60,"tick_before":0}',
        })
        bridge = FactorioBridge(mock)  # type: ignore[arg-type]
        bridge.auto_advance = False

        result = bridge.call_script("place", '{"name":"chest"}')

        # Should only send place, no auto-advance
        assert len(mock.commands_sent) == 1

    def test_error_no_auto_advance(self):
        mock = MockRCON({
            "place": '{"error":"cannot place"}',
        })
        bridge = FactorioBridge(mock)  # type: ignore[arg-type]

        with pytest.raises(ScriptError):
            bridge.call_script("place", '{"name":"chest"}')

        # Should only send place, no advance on error
        assert len(mock.commands_sent) == 1


class TestReloadScript:
    """Test reload_script and reload_all methods."""

    def test_reload_script_calls_rcon(self):
        """reload_script sends correct RCON command."""
        mock = MockRCON({"reload": '{"ok": true, "reloaded": "atomic.test"}'})
        bridge = FactorioBridge(mock)  # type: ignore[arg-type]

        result = bridge.reload_script("atomic.test")

        mock_rcon_cmd = mock.commands_sent[0]
        assert "reload" in mock_rcon_cmd
        assert "atomic.test" in mock_rcon_cmd
        assert result["ok"] is True
        assert result["reloaded"] == "atomic.test"

    def test_reload_all_calls_rcon(self):
        """reload_all sends reload_all command."""
        mock = MockRCON({"reload_all": '{"ok": true, "reloaded": "all"}'})
        bridge = FactorioBridge(mock)  # type: ignore[arg-type]

        result = bridge.reload_all()

        assert "reload_all" in mock.commands_sent[0]
        assert result["reloaded"] == "all"
