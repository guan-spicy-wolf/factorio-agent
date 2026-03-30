"""Tool system: @tool decorator, auto JSON schema, registry.

Usage:
    @tool
    def api_search(query: str, limit: int = 20) -> dict:
        '''Search the Factorio API docs.'''
        ...

    registry = ToolRegistry()
    registry.register(api_search)
    schema = registry.schemas()        # Anthropic-compatible tool definitions
    result = registry.call("api_search", {"query": "fluid"})
"""

import inspect
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, get_type_hints

logger = logging.getLogger(__name__)

# Python type → JSON Schema type
_TYPE_MAP: dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}


@dataclass
class ToolDef:
    """A registered tool with its metadata and callable."""

    name: str
    description: str
    fn: Callable
    parameters: dict  # JSON Schema for input_schema


def _build_schema(fn: Callable) -> dict:
    """Generate JSON Schema from function signature and type hints."""
    sig = inspect.signature(fn)
    hints = get_type_hints(fn)
    properties: dict[str, Any] = {}
    required: list[str] = []

    for param_name, param in sig.parameters.items():
        if param_name in ("self", "cls"):
            continue

        hint = hints.get(param_name, str)
        json_type = _TYPE_MAP.get(hint, "string")
        prop: dict[str, Any] = {"type": json_type}

        if param.default is not inspect.Parameter.empty:
            prop["default"] = param.default
        else:
            required.append(param_name)

        properties[param_name] = prop

    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
    }
    if required:
        schema["required"] = required
    return schema


def _extract_description(fn: Callable) -> str:
    """Extract first paragraph from docstring."""
    doc = inspect.getdoc(fn)
    if not doc:
        return fn.__name__
    # First paragraph = up to first blank line
    lines = []
    for line in doc.split("\n"):
        if line.strip() == "":
            if lines:
                break
            continue
        lines.append(line.strip())
    return " ".join(lines)


def tool(fn: Callable) -> Callable:
    """Decorator that marks a function as an agent tool.

    Attaches _tool_def metadata for later registration.
    """
    fn._tool_def = ToolDef(  # type: ignore[attr-defined]
        name=fn.__name__,
        description=_extract_description(fn),
        fn=fn,
        parameters=_build_schema(fn),
    )
    return fn


class ToolRegistry:
    """Registry of callable tools with Anthropic-compatible schema export."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDef] = {}

    def register(self, fn_or_def: Callable | ToolDef) -> None:
        """Register a @tool-decorated function or a ToolDef directly."""
        if isinstance(fn_or_def, ToolDef):
            td = fn_or_def
        elif hasattr(fn_or_def, "_tool_def"):
            td = fn_or_def._tool_def  # type: ignore[attr-defined]
        else:
            raise ValueError(
                f"{fn_or_def} is not decorated with @tool and is not a ToolDef"
            )
        self._tools[td.name] = td

    def schemas(self) -> list[dict]:
        """Export all tools as Anthropic API tool definitions."""
        return [
            {
                "name": td.name,
                "description": td.description,
                "input_schema": td.parameters,
            }
            for td in self._tools.values()
        ]

    def call(self, name: str, arguments: dict) -> Any:
        """Execute a tool by name with the given arguments."""
        td = self._tools.get(name)
        if not td:
            return {"error": f"unknown tool: {name}"}

        try:
            return td.fn(**arguments)
        except Exception as e:
            logger.exception("Tool %s raised an error", name)
            return {"error": f"{type(e).__name__}: {e}"}

    def has(self, name: str) -> bool:
        return name in self._tools

    @property
    def names(self) -> list[str]:
        return list(self._tools.keys())
