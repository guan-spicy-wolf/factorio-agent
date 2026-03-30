"""Tests for the tool system."""

import pytest
from agent.tools import tool, ToolRegistry, ToolDef, _build_schema


# --- @tool decorator ---

class TestToolDecorator:
    def test_attaches_metadata(self):
        @tool
        def my_tool(x: str) -> str:
            """Do something."""
            return x

        assert hasattr(my_tool, "_tool_def")
        td = my_tool._tool_def
        assert td.name == "my_tool"
        assert td.description == "Do something."
        assert td.fn is my_tool

    def test_still_callable(self):
        @tool
        def add(a: int, b: int) -> int:
            """Add two numbers."""
            return a + b

        assert add(1, 2) == 3

    def test_multiline_docstring(self):
        @tool
        def fancy(x: str) -> str:
            """First paragraph of description.

            This second paragraph should not be included.
            """
            return x

        assert fancy._tool_def.description == "First paragraph of description."

    def test_no_docstring(self):
        @tool
        def bare(x: str) -> str:
            return x

        assert bare._tool_def.description == "bare"


# --- Schema generation ---

class TestBuildSchema:
    def test_required_params(self):
        def fn(name: str, count: int):
            pass

        schema = _build_schema(fn)
        assert schema["required"] == ["name", "count"]
        assert schema["properties"]["name"]["type"] == "string"
        assert schema["properties"]["count"]["type"] == "integer"

    def test_optional_params(self):
        def fn(query: str, limit: int = 20):
            pass

        schema = _build_schema(fn)
        assert schema["required"] == ["query"]
        assert schema["properties"]["limit"]["default"] == 20

    def test_no_params(self):
        def fn():
            pass

        schema = _build_schema(fn)
        assert schema["properties"] == {}
        assert "required" not in schema

    def test_bool_and_float(self):
        def fn(flag: bool, ratio: float):
            pass

        schema = _build_schema(fn)
        assert schema["properties"]["flag"]["type"] == "boolean"
        assert schema["properties"]["ratio"]["type"] == "number"

    def test_skips_self(self):
        def fn(self, x: str):
            pass

        schema = _build_schema(fn)
        assert "self" not in schema["properties"]


# --- ToolRegistry ---

class TestToolRegistry:
    def test_register_and_call(self):
        @tool
        def greet(name: str) -> str:
            """Say hello."""
            return f"Hello, {name}!"

        reg = ToolRegistry()
        reg.register(greet)

        assert reg.has("greet")
        assert reg.call("greet", {"name": "Alice"}) == "Hello, Alice!"

    def test_schemas(self):
        @tool
        def search(query: str, limit: int = 10) -> list:
            """Search for things."""
            return []

        reg = ToolRegistry()
        reg.register(search)
        schemas = reg.schemas()

        assert len(schemas) == 1
        s = schemas[0]
        assert s["name"] == "search"
        assert s["description"] == "Search for things."
        assert s["input_schema"]["properties"]["query"]["type"] == "string"

    def test_call_unknown_tool(self):
        reg = ToolRegistry()
        result = reg.call("nope", {})
        assert "error" in result

    def test_call_handles_exception(self):
        @tool
        def explode() -> str:
            """Boom."""
            raise ValueError("kaboom")

        reg = ToolRegistry()
        reg.register(explode)
        result = reg.call("explode", {})
        assert "error" in result
        assert "kaboom" in result["error"]

    def test_names(self):
        @tool
        def a() -> str:
            """A."""
            return "a"

        @tool
        def b() -> str:
            """B."""
            return "b"

        reg = ToolRegistry()
        reg.register(a)
        reg.register(b)
        assert set(reg.names) == {"a", "b"}

    def test_register_tooldef_directly(self):
        def raw(x: str) -> str:
            return x

        td = ToolDef(
            name="raw_tool",
            description="A raw tool",
            fn=raw,
            parameters={"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]},
        )
        reg = ToolRegistry()
        reg.register(td)
        assert reg.call("raw_tool", {"x": "hi"}) == "hi"

    def test_register_non_tool_raises(self):
        def plain():
            pass

        reg = ToolRegistry()
        with pytest.raises(ValueError, match="not decorated"):
            reg.register(plain)
