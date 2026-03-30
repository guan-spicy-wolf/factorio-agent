"""Simple file-based memory for the agent.

Provides read/write/append tools that operate on a local markdown file.
Memory persists across agent runs, giving the LLM a scratchpad for
observations, plans, learned patterns, and game state notes.
"""

import logging
from pathlib import Path

from agent.tools import tool

logger = logging.getLogger(__name__)

DEFAULT_MEMORY_PATH = Path("memory/agent_notes.md")

# Module-level path, set by init_memory() before tools are used.
_memory_path: Path = DEFAULT_MEMORY_PATH


def init_memory(path: Path | str = DEFAULT_MEMORY_PATH) -> None:
    """Set the memory file path and ensure directory exists."""
    global _memory_path
    _memory_path = Path(path)
    _memory_path.parent.mkdir(parents=True, exist_ok=True)
    if not _memory_path.exists():
        _memory_path.write_text("# Agent Memory\n\n", encoding="utf-8")
        logger.info("Created memory file: %s", _memory_path)


@tool
def memory_read() -> str:
    """Read the full contents of the agent's persistent memory file."""
    if not _memory_path.exists():
        return "(memory is empty)"
    return _memory_path.read_text(encoding="utf-8")


@tool
def memory_write(content: str) -> str:
    """Overwrite the agent's memory file with new content.

    Use this to reorganize or clean up memory. For adding new notes,
    prefer memory_append.
    """
    _memory_path.parent.mkdir(parents=True, exist_ok=True)
    _memory_path.write_text(content, encoding="utf-8")
    return f"Memory updated ({len(content)} chars)"


@tool
def memory_append(note: str) -> str:
    """Append a note to the agent's memory file.

    Use this to record observations, decisions, learned patterns,
    or anything worth remembering for future runs.
    """
    _memory_path.parent.mkdir(parents=True, exist_ok=True)
    with open(_memory_path, "a", encoding="utf-8") as f:
        f.write(f"\n{note}\n")
    return "Note appended to memory"
