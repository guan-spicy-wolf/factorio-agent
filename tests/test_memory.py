"""Tests for the agent memory system."""

import pytest
from pathlib import Path

from agent.memory import init_memory, memory_read, memory_write, memory_append


@pytest.fixture
def mem_path(tmp_path):
    p = tmp_path / "test_memory.md"
    init_memory(p)
    return p


class TestMemory:
    def test_init_creates_file(self, mem_path):
        assert mem_path.exists()
        content = mem_path.read_text()
        assert "Agent Memory" in content

    def test_read_empty(self, mem_path):
        result = memory_read()
        assert "Agent Memory" in result

    def test_write_and_read(self, mem_path):
        memory_write("# New content\n\nHello world")
        result = memory_read()
        assert "Hello world" in result
        assert "Agent Memory" not in result  # overwritten

    def test_append(self, mem_path):
        memory_append("First note")
        memory_append("Second note")
        result = memory_read()
        assert "First note" in result
        assert "Second note" in result
        assert "Agent Memory" in result  # header preserved

    def test_write_returns_length(self, mem_path):
        result = memory_write("short")
        assert "5 chars" in result

    def test_append_returns_confirmation(self, mem_path):
        result = memory_append("test")
        assert "appended" in result.lower()

    def test_read_nonexistent(self, tmp_path):
        init_memory(tmp_path / "nonexistent" / "memory.md")
        # init_memory creates the file, so it should exist
        result = memory_read()
        assert "Agent Memory" in result
