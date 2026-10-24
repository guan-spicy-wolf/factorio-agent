"""Blueprint JSON -> Layout.

Only entities are imported; blueprint-level fields (``wires``, ``tiles``, ``schedules``)
are not part of ``Layout`` and are dropped.
"""

from __future__ import annotations

from typing import Any

from fgen.blueprint.codec import unpack_version
from fgen.blueprint.export import RESERVED_KEYS
from fgen.geom.vec import Direction, Vec2
from fgen.layout.model import Layout, Placement


def _inner(bp: dict[str, Any]) -> dict[str, Any]:
    if "blueprint" in bp:
        return bp["blueprint"]
    if "blueprint_book" in bp:
        raise ValueError("blueprint books are not supported; pass a single blueprint")
    if "entities" in bp or bp.get("item") == "blueprint":
        return bp
    raise ValueError(f"not a blueprint object (keys: {sorted(bp)})")


def blueprint_to_layout(bp: dict[str, Any]) -> Layout:
    inner = _inner(bp)
    legacy = unpack_version(int(inner.get("version", 0)))[0] < 2 and "version" in inner
    ents = sorted(inner.get("entities", []), key=lambda e: e.get("entity_number", 0))
    placements = []
    for e in ents:
        raw_dir = int(e.get("direction", 0))
        d = Direction.from_legacy_8way(raw_dir) if legacy else Direction.from_blueprint(raw_dir)
        pos = e["position"]
        placements.append(
            Placement(
                name=e["name"],
                position=Vec2(float(pos["x"]), float(pos["y"])),
                direction=d,
                extra={k: v for k, v in e.items() if k not in RESERVED_KEYS},
            )
        )
    return Layout(placements)
