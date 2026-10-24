"""Layout / Unit -> blueprint JSON."""

from __future__ import annotations

from collections import Counter
from typing import Any

from fgen.blueprint.codec import pack_version
from fgen.data.schema import GameData
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
                # Always written, even for N (0): conservative, see VERIFY.md.
                "direction": p.direction.to_blueprint(),
                **p.extra,
            }
        )
    return out


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
    if icon is not None:
        bp["icons"] = [{"signal": {"type": "item", "name": icon}, "index": 1}]
    if label is not None:
        bp["label"] = label
    return {"blueprint": bp}


def unit_to_blueprint(unit: Unit, gd: GameData, label: str | None = None) -> dict[str, Any]:
    return layout_to_blueprint(unit.layout, gd, label=label, icon=unit.meta.get("icon"))
