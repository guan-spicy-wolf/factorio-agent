"""Layout / Unit -> blueprint JSON."""

from __future__ import annotations

from collections import Counter
from typing import Any

from fgen.blueprint.codec import pack_version
from fgen.data.schema import GameData
from fgen.geom import pole
from fgen.layout.model import Layout, Unit

RESERVED_KEYS = frozenset({"entity_number", "name", "position", "direction"})


def _num(v: float) -> float | int:
    return int(v) if float(v).is_integer() else v


def entity_dicts(layout: Layout) -> list[dict[str, Any]]:
    out = []
    for i, p in enumerate(layout.placements, start=1):
        clash = RESERVED_KEYS & p.extra.keys()
        if clash:
            raise ValueError(f"placement extra may not override {sorted(clash)}")
        out.append(
            {
                "entity_number": i,
                "name": p.name,
                "position": {"x": _num(p.position.x), "y": _num(p.position.y)},
                # Always written, even for N (0); 2.0.76 accepts it (fgen verify #5).
                "direction": p.direction.to_blueprint(),
                **p.extra,
            }
        )
    return out


def pole_wires(layout: Layout, gd: GameData) -> list[list[int]]:
    """2.0 blueprint-level ``wires``: ``[entity_number, connector, entity_number,
    connector]`` per copper wire, a spanning tree over poles in wire reach."""
    copper = int(gd.constant("wire_connector_pole_copper"))
    idx = [
        i
        for i, p in enumerate(layout.placements)
        if gd.entities.get(p.name) is not None and gd.entity(p.name).type == "electric-pole"
    ]
    poles = [(layout.placements[i].name, layout.placements[i].position) for i in idx]
    return [[idx[a] + 1, copper, idx[b] + 1, copper] for a, b in pole.copper_tree(gd, poles)]


def layout_to_blueprint(
    layout: Layout, gd: GameData, label: str | None = None, icon: str | None = None
) -> dict[str, Any]:
    if icon is None and layout.placements:
        icon = Counter(p.name for p in layout.placements).most_common(1)[0][0]
    bp: dict[str, Any] = {
        "item": "blueprint",
        "entities": entity_dicts(layout),
        "version": pack_version(gd.version),
    }
    wires = pole_wires(layout, gd)
    if wires:
        bp["wires"] = wires
    if icon is not None:
        bp["icons"] = [{"signal": {"type": "item", "name": icon}, "index": 1}]
    if label is not None:
        bp["label"] = label
    return {"blueprint": bp}


def unit_to_blueprint(unit: Unit, gd: GameData, label: str | None = None) -> dict[str, Any]:
    return layout_to_blueprint(unit.layout, gd, label=label, icon=unit.meta.get("icon"))
