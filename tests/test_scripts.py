"""Tests for script management."""

import pytest
from pathlib import Path
from agent.scripts import write_script, list_scripts, read_script


class TestWriteScript:
    """Test write_script function."""

    def test_write_new_script(self, tmp_path, monkeypatch):
        """write_script creates new script file."""
        monkeypatch.setattr("agent.scripts.MOD_SCRIPTS_DIR", tmp_path)

        code = "return function() return 'test' end"
        result = write_script("atomic.test_script", code)

        assert result["created"] is True
        assert result["name"] == "atomic.test_script"
        assert "atomic/test_script.lua" in result["path"]

    def test_write_updates_existing(self, tmp_path, monkeypatch):
        """write_script updates existing script."""
        monkeypatch.setattr("agent.scripts.MOD_SCRIPTS_DIR", tmp_path)

        # Write first
        write_script("atomic.update_test", "return function() end")

        # Update
        code = "return function() return 'updated' end"
        result = write_script("atomic.update_test", code)

        assert result["updated"] is True
        assert result["created"] is False


class TestWriteScriptWithReload:
    """Test write_script with reload_callback."""

    def test_write_script_with_reload_callback(self, tmp_path, monkeypatch):
        """write_script with reload_callback returns activation: immediate."""
        monkeypatch.setattr("agent.scripts.MOD_SCRIPTS_DIR", tmp_path)

        code = "return function() return 'test' end"

        # Mock successful reload
        def mock_reload(name):
            return {"ok": True, "reloaded": name}

        result = write_script("atomic.test_reload", code, reload_callback=mock_reload)

        assert result["activation"] == "immediate"
        assert result["created"] is True

    def test_write_script_without_reload_returns_restart_required(self, tmp_path, monkeypatch):
        """write_script without reload_callback returns restart_required."""
        monkeypatch.setattr("agent.scripts.MOD_SCRIPTS_DIR", tmp_path)

        code = "return function() return 'test' end"
        result = write_script("atomic.test_no_reload", code)

        assert result["activation"] == "restart_required"

    def test_write_script_reload_failed(self, tmp_path, monkeypatch):
        """write_script with failing reload returns reload_failed."""
        monkeypatch.setattr("agent.scripts.MOD_SCRIPTS_DIR", tmp_path)

        code = "return function() return 'test' end"

        # Mock failed reload
        def mock_reload(name):
            return {"ok": False, "error": "no connection"}

        result = write_script("atomic.test_reload_fail", code, reload_callback=mock_reload)

        assert result["activation"] == "reload_failed"


class TestListScripts:
    """Test list_scripts function."""

    def test_list_scripts_categories(self, tmp_path, monkeypatch):
        """list_scripts returns all categories."""
        monkeypatch.setattr("agent.scripts.MOD_SCRIPTS_DIR", tmp_path)

        # Create some scripts
        (tmp_path / "atomic").mkdir()
        (tmp_path / "atomic" / "test.lua").write_text("return function() end")
        (tmp_path / "actions").mkdir()
        (tmp_path / "actions" / "test.lua").write_text("return function() end")

        result = list_scripts()

        assert "atomic" in result
        assert "actions" in result
        assert "examples" in result
        assert "lib" in result
        assert "test" in result["atomic"]
        assert "test" in result["actions"]


class TestReadScript:
    """Test read_script function."""

    def test_read_existing_script(self, tmp_path, monkeypatch):
        """read_script returns code of existing script."""
        monkeypatch.setattr("agent.scripts.MOD_SCRIPTS_DIR", tmp_path)

        # Create a script
        (tmp_path / "atomic").mkdir(parents=True)
        code = "return function() return 'hello' end"
        (tmp_path / "atomic" / "existing.lua").write_text(code)

        result = read_script("atomic.existing")

        assert result["code"] == code
        assert "atomic/existing.lua" in result["path"]

    def test_read_nonexistent_script(self, tmp_path, monkeypatch):
        """read_script returns error for nonexistent script."""
        monkeypatch.setattr("agent.scripts.MOD_SCRIPTS_DIR", tmp_path)

        result = read_script("atomic.nonexistent")

        assert "error" in result
        assert "not found" in result["error"]