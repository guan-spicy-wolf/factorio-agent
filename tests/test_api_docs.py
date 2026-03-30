"""Tests for the API documentation index and search."""

import json
import pytest
from pathlib import Path

from agent.api_docs import ApiIndex, _type_to_str, ApiEntry


# --- Fixtures: minimal fake API data ---

MINIMAL_RUNTIME = {
    "classes": [
        {
            "name": "LuaGameScript",
            "order": 0,
            "description": "The main scripting interface.",
            "abstract": False,
            "methods": [
                {
                    "name": "create_force",
                    "order": 0,
                    "description": "Create a new force.",
                    "parameters": [
                        {
                            "name": "name",
                            "order": 0,
                            "description": "Name of the force.",
                            "type": "string",
                            "optional": False,
                        }
                    ],
                    "format": {"takes_table": False},
                    "return_values": [
                        {
                            "order": 0,
                            "description": "The created force.",
                            "type": "LuaForce",
                            "optional": False,
                        }
                    ],
                }
            ],
            "attributes": [
                {
                    "name": "tick",
                    "order": 0,
                    "description": "Current map tick.",
                    "read_type": "uint",
                    "optional": False,
                }
            ],
            "operators": [],
        }
    ],
    "events": [
        {
            "name": "on_tick",
            "order": 0,
            "description": "Called every game tick.",
            "data": [
                {
                    "name": "tick",
                    "order": 0,
                    "description": "Tick number.",
                    "type": "uint",
                    "optional": False,
                }
            ],
        }
    ],
    "concepts": [
        {
            "name": "MapPosition",
            "order": 0,
            "description": "Coordinates of a tile.",
            "type": {
                "complex_type": "table",
                "parameters": [
                    {"name": "x", "order": 0, "description": "", "type": "double", "optional": False},
                    {"name": "y", "order": 1, "description": "", "type": "double", "optional": False},
                ],
            },
        }
    ],
    "defines": [
        {
            "name": "direction",
            "order": 0,
            "description": "Cardinal directions.",
            "values": [
                {"name": "north", "order": 0, "description": ""},
                {"name": "east", "order": 1, "description": ""},
                {"name": "south", "order": 2, "description": ""},
                {"name": "west", "order": 3, "description": ""},
            ],
        }
    ],
    "global_objects": [
        {
            "name": "game",
            "order": 0,
            "description": "Main scripting interface.",
            "type": "LuaGameScript",
        }
    ],
}

MINIMAL_PROTOTYPE = {
    "prototypes": [
        {
            "name": "TransportBeltPrototype",
            "order": 0,
            "description": "A transport belt.",
            "parent": "EntityPrototype",
            "abstract": False,
            "typename": "transport-belt",
            "properties": [
                {
                    "name": "speed",
                    "order": 0,
                    "description": "Belt speed in tiles per tick.",
                    "override": False,
                    "type": "double",
                    "optional": False,
                }
            ],
        }
    ],
    "types": [
        {
            "name": "Color",
            "order": 0,
            "description": "An RGBA color value.",
            "properties": [
                {
                    "name": "r",
                    "order": 0,
                    "description": "Red.",
                    "type": "float",
                    "optional": True,
                },
                {
                    "name": "g",
                    "order": 1,
                    "description": "Green.",
                    "type": "float",
                    "optional": True,
                },
            ],
        }
    ],
}


@pytest.fixture
def tmp_api_files(tmp_path):
    """Write minimal API JSON files and return their paths."""
    runtime = tmp_path / "runtime-api.json"
    prototype = tmp_path / "prototype-api.json"
    runtime.write_text(json.dumps(MINIMAL_RUNTIME))
    prototype.write_text(json.dumps(MINIMAL_PROTOTYPE))
    return runtime, prototype


@pytest.fixture
def index(tmp_api_files):
    runtime, prototype = tmp_api_files
    idx = ApiIndex(runtime_path=runtime, prototype_path=prototype)
    idx.load()
    return idx


# --- type_to_str ---

class TestTypeToStr:
    def test_simple_string(self):
        assert _type_to_str("uint") == "uint"

    def test_union(self):
        t = {"complex_type": "union", "options": ["string", "uint"]}
        assert _type_to_str(t) == "string | uint"

    def test_array(self):
        t = {"complex_type": "array", "value": "LuaEntity"}
        assert _type_to_str(t) == "array<LuaEntity>"

    def test_dictionary(self):
        t = {"complex_type": "dictionary", "key": "string", "value": "uint"}
        assert _type_to_str(t) == "dict<string, uint>"

    def test_table(self):
        t = {
            "complex_type": "table",
            "parameters": [
                {"name": "x", "type": "double"},
                {"name": "y", "type": "double"},
            ],
        }
        assert _type_to_str(t) == "{x: double, y: double}"

    def test_literal(self):
        t = {"complex_type": "literal", "value": 42}
        assert _type_to_str(t) == "42"

    def test_nested(self):
        t = {"complex_type": "array", "value": {"complex_type": "union", "options": ["string", "uint"]}}
        assert _type_to_str(t) == "array<string | uint>"


# --- Index loading ---

class TestIndexLoading:
    def test_loads_entries(self, index):
        assert index.entry_count > 0

    def test_stats(self, index):
        s = index.stats()
        assert s["total"] > 0
        assert "class" in s["by_kind"]
        assert "method" in s["by_kind"]
        assert "prototype" in s["by_kind"]

    def test_missing_files(self, tmp_path):
        idx = ApiIndex(
            runtime_path=tmp_path / "nope.json",
            prototype_path=tmp_path / "nope2.json",
        )
        idx.load()
        assert idx.entry_count == 0


# --- Search ---

class TestSearch:
    def test_search_by_class_name(self, index):
        results = index.search("LuaGameScript")
        assert len(results) > 0
        assert results[0]["name"] == "LuaGameScript"

    def test_search_by_method_name(self, index):
        results = index.search("create_force")
        names = [r["name"] for r in results]
        assert "LuaGameScript.create_force" in names

    def test_search_by_keyword_in_description(self, index):
        results = index.search("transport belt")
        names = [r["name"] for r in results]
        assert "TransportBeltPrototype" in names

    def test_search_case_insensitive(self, index):
        r1 = index.search("luagamescript")
        r2 = index.search("LUAGAMESCRIPT")
        assert r1[0]["name"] == r2[0]["name"]

    def test_search_no_results(self, index):
        results = index.search("xyzzy_nonexistent_thing")
        assert results == []

    def test_search_event(self, index):
        results = index.search("on_tick")
        names = [r["name"] for r in results]
        assert "on_tick" in names

    def test_search_define(self, index):
        results = index.search("direction")
        names = [r["name"] for r in results]
        assert "defines.direction" in names

    def test_search_concept(self, index):
        results = index.search("MapPosition")
        assert results[0]["name"] == "MapPosition"

    def test_search_limit(self, index):
        results = index.search("a", limit=2)
        assert len(results) <= 2


# --- Detail ---

class TestDetail:
    def test_detail_class(self, index):
        d = index.detail("LuaGameScript")
        assert d is not None
        assert d["kind"] == "class"
        assert "methods" in d
        assert "create_force" in d["methods"]
        assert "attributes" in d
        assert "tick" in d["attributes"]

    def test_detail_method(self, index):
        d = index.detail("LuaGameScript.create_force")
        assert d is not None
        assert d["kind"] == "method"
        assert d["parent"] == "LuaGameScript"
        assert len(d["parameters"]) == 1
        assert d["parameters"][0]["name"] == "name"
        assert len(d["return_values"]) == 1

    def test_detail_attribute(self, index):
        d = index.detail("LuaGameScript.tick")
        assert d is not None
        assert d["kind"] == "attribute"
        assert d["type"] == "uint"

    def test_detail_event(self, index):
        d = index.detail("on_tick")
        assert d is not None
        assert d["kind"] == "event"
        assert "data" in d
        assert d["data"][0]["name"] == "tick"

    def test_detail_define(self, index):
        d = index.detail("defines.direction")
        assert d is not None
        assert d["kind"] == "define"
        assert "values" in d
        assert "north" in d["values"]

    def test_detail_define_value(self, index):
        d = index.detail("defines.direction.north")
        assert d is not None
        assert d["kind"] == "define_value"

    def test_detail_prototype(self, index):
        d = index.detail("TransportBeltPrototype")
        assert d is not None
        assert d["kind"] == "prototype"
        assert d["parent"] == "EntityPrototype"
        assert "properties" in d
        assert d["properties"][0]["name"] == "speed"

    def test_detail_type(self, index):
        d = index.detail("Color")
        assert d is not None
        assert d["kind"] == "type"

    def test_detail_concept(self, index):
        d = index.detail("MapPosition")
        assert d is not None
        assert d["kind"] == "concept"
        assert d["type"] == "{x: double, y: double}"

    def test_detail_not_found(self, index):
        assert index.detail("NoSuchThing") is None

    def test_detail_case_insensitive(self, index):
        d = index.detail("luagamescript")
        assert d is not None
        assert d["name"] == "LuaGameScript"

    def test_detail_global_object(self, index):
        d = index.detail("game")
        assert d is not None
        assert d["kind"] == "global"


# --- Integration with real files (if available) ---

class TestRealFiles:
    """Tests against the actual API JSON files. Skipped if not present."""

    @pytest.fixture
    def real_index(self):
        if not MINIMAL_RUNTIME:  # always true, just using the class
            pytest.skip("placeholder")
        from agent.api_docs import RUNTIME_API_PATH, PROTOTYPE_API_PATH
        if not RUNTIME_API_PATH.exists():
            pytest.skip("runtime-api.json not found")
        idx = ApiIndex()
        idx.load()
        return idx

    def test_real_stats(self, real_index):
        s = real_index.stats()
        # Expect substantial entries from real docs
        assert s["total"] > 1000
        print(f"Total entries: {s['total']}")
        print(f"By kind: {s['by_kind']}")

    def test_real_search_fluid(self, real_index):
        results = real_index.search("fluid")
        assert len(results) > 0
        print("Search 'fluid':", [r["name"] for r in results[:5]])

    def test_real_search_surface(self, real_index):
        results = real_index.search("LuaSurface")
        assert len(results) > 0
        assert results[0]["name"] == "LuaSurface"

    def test_real_detail_surface(self, real_index):
        d = real_index.detail("LuaSurface")
        assert d is not None
        assert "methods" in d
        assert len(d["methods"]) > 10
