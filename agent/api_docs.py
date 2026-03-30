"""Factorio API documentation index and search.

Parses the official runtime-api.json and prototype-api.json into a flat
searchable index. Provides two query interfaces:

    api_search(query)  → list of matching entries (name, kind, summary)
    api_detail(name)   → full entry with params, return values, description
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

# Default locations relative to project root
_FILES_DIR = Path(__file__).resolve().parent.parent / "files"
RUNTIME_API_PATH = _FILES_DIR / "runtime-api.json"
PROTOTYPE_API_PATH = _FILES_DIR / "prototype-api.json"

MAX_SEARCH_RESULTS = 20


@dataclass
class ApiEntry:
    """A single searchable item in the API index."""

    qualified_name: str  # e.g. "LuaGameScript.create_force"
    kind: str  # class, method, attribute, event, concept, prototype, property, define
    source: str  # "runtime" or "prototype"
    description: str = ""
    parameters: list[dict] = field(default_factory=list)
    return_values: list[dict] = field(default_factory=list)
    type_info: str = ""  # for attributes/properties: type as string
    parent: str = ""  # parent class/prototype name
    raw: dict = field(default_factory=dict, repr=False)


def _type_to_str(t) -> str:
    """Convert a type field (string or complex_type dict) to readable string."""
    if isinstance(t, str):
        return t
    if isinstance(t, dict):
        ct = t.get("complex_type", "")
        if ct == "union":
            options = t.get("options", [])
            return " | ".join(_type_to_str(o) for o in options)
        if ct == "array":
            return f"array<{_type_to_str(t.get('value', '?'))}>"
        if ct == "dictionary":
            return f"dict<{_type_to_str(t.get('key', '?'))}, {_type_to_str(t.get('value', '?'))}>"
        if ct == "LuaCustomTable":
            return f"LuaCustomTable<{_type_to_str(t.get('key', '?'))}, {_type_to_str(t.get('value', '?'))}>"
        if ct == "function":
            params = t.get("parameters", [])
            param_strs = [_type_to_str(p) for p in params]
            return f"fun({', '.join(param_strs)})"
        if ct == "literal":
            return repr(t.get("value", ""))
        if ct == "type":
            return _type_to_str(t.get("value", "?"))
        if ct == "table":
            params = t.get("parameters", [])
            fields = [f"{p.get('name','?')}: {_type_to_str(p.get('type','?'))}" for p in params]
            return "{" + ", ".join(fields) + "}"
        if ct == "tuple":
            values = t.get("values", [])
            return f"tuple<{', '.join(_type_to_str(v) for v in values)}>"
        if ct == "LuaLazyLoadedValue":
            return f"LuaLazyLoadedValue<{_type_to_str(t.get('value', '?'))}>"
        if ct == "LuaStruct":
            attrs = t.get("attributes", [])
            fields = [f"{a.get('name','?')}: {_type_to_str(a.get('type','?'))}" for a in attrs]
            return "struct{" + ", ".join(fields) + "}"
        # Fallback: return the complex_type name
        return ct if ct else str(t)
    return str(t)


def _truncate(s: str, max_len: int = 120) -> str:
    if len(s) <= max_len:
        return s
    return s[: max_len - 3] + "..."


def _build_runtime_index(data: dict) -> list[ApiEntry]:
    """Index classes, methods, attributes, events, concepts, defines."""
    entries: list[ApiEntry] = []

    # Classes
    for cls in data.get("classes", []):
        cls_name = cls["name"]
        entries.append(ApiEntry(
            qualified_name=cls_name,
            kind="class",
            source="runtime",
            description=cls.get("description", ""),
            raw=cls,
        ))
        # Methods
        for m in cls.get("methods", []):
            entries.append(ApiEntry(
                qualified_name=f"{cls_name}.{m['name']}",
                kind="method",
                source="runtime",
                description=m.get("description", ""),
                parameters=m.get("parameters", []),
                return_values=m.get("return_values", []),
                parent=cls_name,
                raw=m,
            ))
        # Attributes
        for a in cls.get("attributes", []):
            type_str = _type_to_str(a.get("read_type", a.get("type", "")))
            entries.append(ApiEntry(
                qualified_name=f"{cls_name}.{a['name']}",
                kind="attribute",
                source="runtime",
                description=a.get("description", ""),
                type_info=type_str,
                parent=cls_name,
                raw=a,
            ))

    # Events
    for ev in data.get("events", []):
        entries.append(ApiEntry(
            qualified_name=ev["name"],
            kind="event",
            source="runtime",
            description=ev.get("description", ""),
            raw=ev,
        ))

    # Concepts
    for co in data.get("concepts", []):
        entries.append(ApiEntry(
            qualified_name=co["name"],
            kind="concept",
            source="runtime",
            description=co.get("description", ""),
            type_info=_type_to_str(co.get("type", "")),
            raw=co,
        ))

    # Defines
    for df in data.get("defines", []):
        _index_define(entries, df, "")

    # Global objects
    for go in data.get("global_objects", []):
        entries.append(ApiEntry(
            qualified_name=go["name"],
            kind="global",
            source="runtime",
            description=go.get("description", ""),
            type_info=_type_to_str(go.get("type", "")),
            raw=go,
        ))

    return entries


def _index_define(entries: list[ApiEntry], df: dict, prefix: str) -> None:
    """Recursively index defines and their sub-defines."""
    name = f"{prefix}.{df['name']}" if prefix else f"defines.{df['name']}"
    entries.append(ApiEntry(
        qualified_name=name,
        kind="define",
        source="runtime",
        description=df.get("description", ""),
        raw=df,
    ))
    for sub in df.get("subkeys", []):
        _index_define(entries, sub, name)
    for val in df.get("values", []):
        entries.append(ApiEntry(
            qualified_name=f"{name}.{val['name']}",
            kind="define_value",
            source="runtime",
            description=val.get("description", ""),
            raw=val,
        ))


def _build_prototype_index(data: dict) -> list[ApiEntry]:
    """Index prototypes, their properties, and types."""
    entries: list[ApiEntry] = []

    for proto in data.get("prototypes", []):
        proto_name = proto["name"]
        entries.append(ApiEntry(
            qualified_name=proto_name,
            kind="prototype",
            source="prototype",
            description=proto.get("description", ""),
            parent=proto.get("parent", ""),
            raw=proto,
        ))
        for prop in proto.get("properties", []):
            entries.append(ApiEntry(
                qualified_name=f"{proto_name}.{prop['name']}",
                kind="property",
                source="prototype",
                description=prop.get("description", ""),
                type_info=_type_to_str(prop.get("type", "")),
                parent=proto_name,
                raw=prop,
            ))

    for t in data.get("types", []):
        type_name = t["name"]
        entries.append(ApiEntry(
            qualified_name=type_name,
            kind="type",
            source="prototype",
            description=t.get("description", ""),
            raw=t,
        ))
        for prop in t.get("properties", []):
            entries.append(ApiEntry(
                qualified_name=f"{type_name}.{prop['name']}",
                kind="property",
                source="prototype",
                description=prop.get("description", ""),
                type_info=_type_to_str(prop.get("type", "")),
                parent=type_name,
                raw=prop,
            ))

    return entries


class ApiIndex:
    """Searchable index over Factorio API documentation."""

    def __init__(
        self,
        runtime_path: Path = RUNTIME_API_PATH,
        prototype_path: Path = PROTOTYPE_API_PATH,
    ):
        self._entries: list[ApiEntry] = []
        self._by_name: dict[str, ApiEntry] = {}
        self._loaded = False
        self._runtime_path = runtime_path
        self._prototype_path = prototype_path

    def load(self) -> None:
        """Parse JSON files and build the index."""
        if self._loaded:
            return

        if self._runtime_path.exists():
            with open(self._runtime_path) as f:
                runtime_data = json.load(f)
            self._entries.extend(_build_runtime_index(runtime_data))

        if self._prototype_path.exists():
            with open(self._prototype_path) as f:
                proto_data = json.load(f)
            self._entries.extend(_build_prototype_index(proto_data))

        # Build lookup by qualified_name (last one wins on collision)
        for entry in self._entries:
            self._by_name[entry.qualified_name] = entry
            # Also index by short name (without parent prefix)
            short = entry.qualified_name.rsplit(".", 1)[-1]
            if short not in self._by_name:
                self._by_name[short] = entry

        self._loaded = True

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    def search(self, query: str, limit: int = MAX_SEARCH_RESULTS) -> list[dict]:
        """Search for API entries matching the query.

        Case-insensitive substring match against qualified_name and description.
        Returns a list of summary dicts.
        """
        self._ensure_loaded()
        query_lower = query.lower()
        tokens = query_lower.split()

        scored: list[tuple[int, ApiEntry]] = []
        for entry in self._entries:
            name_lower = entry.qualified_name.lower()
            desc_lower = entry.description.lower()

            # All tokens must match in name or description
            if not all(t in name_lower or t in desc_lower for t in tokens):
                continue

            # Score: prefer name matches over description-only matches
            score = 0
            if query_lower == name_lower:
                score = 100  # exact match
            elif name_lower.endswith("." + query_lower) or name_lower == query_lower:
                score = 90  # exact short name match
            elif query_lower in name_lower:
                score = 50 + (10 if name_lower.startswith(query_lower) else 0)
            else:
                score = 10  # description-only match

            scored.append((score, entry))

        scored.sort(key=lambda x: (-x[0], x[1].qualified_name))

        results = []
        for _, entry in scored[:limit]:
            results.append({
                "name": entry.qualified_name,
                "kind": entry.kind,
                "source": entry.source,
                "summary": _truncate(entry.description),
            })
        return results

    def detail(self, name: str) -> dict | None:
        """Get full details for a specific API entry by name.

        Tries exact match first, then case-insensitive search.
        Returns a detailed dict or None if not found.
        """
        self._ensure_loaded()

        entry = self._by_name.get(name)
        if not entry:
            # Case-insensitive fallback
            name_lower = name.lower()
            for key, e in self._by_name.items():
                if key.lower() == name_lower:
                    entry = e
                    break
        if not entry:
            return None

        result: dict = {
            "name": entry.qualified_name,
            "kind": entry.kind,
            "source": entry.source,
            "description": entry.description,
        }

        if entry.parent:
            result["parent"] = entry.parent

        if entry.type_info:
            result["type"] = entry.type_info

        if entry.parameters:
            result["parameters"] = [
                {
                    "name": p.get("name", ""),
                    "type": _type_to_str(p.get("type", "")),
                    "description": p.get("description", ""),
                    "optional": p.get("optional", False),
                }
                for p in entry.parameters
            ]

        if entry.return_values:
            result["return_values"] = [
                {
                    "type": _type_to_str(rv.get("type", "")),
                    "description": rv.get("description", ""),
                    "optional": rv.get("optional", False),
                }
                for rv in entry.return_values
            ]

        # For classes: list methods and attributes
        if entry.kind == "class":
            raw = entry.raw
            if raw.get("methods"):
                result["methods"] = [m["name"] for m in raw["methods"]]
            if raw.get("attributes"):
                result["attributes"] = [a["name"] for a in raw["attributes"]]

        # For prototypes/types: list properties
        if entry.kind in ("prototype", "type"):
            raw = entry.raw
            if raw.get("properties"):
                result["properties"] = [
                    {
                        "name": p["name"],
                        "type": _type_to_str(p.get("type", "")),
                        "optional": p.get("optional", False),
                    }
                    for p in raw["properties"]
                ]
            if entry.kind == "prototype" and raw.get("parent"):
                result["parent"] = raw["parent"]

        # For defines: list values
        if entry.kind == "define":
            raw = entry.raw
            if raw.get("values"):
                result["values"] = [v["name"] for v in raw["values"]]
            if raw.get("subkeys"):
                result["subkeys"] = [s["name"] for s in raw["subkeys"]]

        # For events: list data fields
        if entry.kind == "event":
            raw = entry.raw
            if raw.get("data"):
                result["data"] = [
                    {
                        "name": d["name"],
                        "type": _type_to_str(d.get("type", "")),
                        "description": d.get("description", ""),
                        "optional": d.get("optional", False),
                    }
                    for d in raw["data"]
                ]

        # Include examples if present
        if entry.raw.get("examples"):
            result["examples"] = entry.raw["examples"]

        return result

    @property
    def entry_count(self) -> int:
        self._ensure_loaded()
        return len(self._entries)

    def stats(self) -> dict:
        """Return index statistics."""
        self._ensure_loaded()
        kinds: dict[str, int] = {}
        for e in self._entries:
            kinds[e.kind] = kinds.get(e.kind, 0) + 1
        return {"total": len(self._entries), "by_kind": kinds}
