"""Order- and noise-insensitive layout comparison (deployer round trips, plan §3.3).

Two layouts are *equal* when they contain the same multiset of entities, where an
entity is ``(name, position rounded to 1/1000 tile, direction, compared extras)``:

- direction is dropped for entities whose direction the game does not keep
  (:func:`fgen.geom.footprint.direction_matters`);
- of ``extra`` only the fields the game reports back are compared: ``type`` of
  underground belts (default ``"input"``, as in blueprints) and ``recipe`` of
  assembling machines. Everything else (``items``, ``tags`` ...) is ignored.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from fgen.data.schema import GameData
from fgen.geom.footprint import direction_matters
from fgen.layout.model import Layout, Placement

type Key = tuple[str, float, float, int | None, tuple[tuple[str, Any], ...]]

NDIGITS = 3


def _extras(gd: GameData, p: Placement) -> tuple[tuple[str, Any], ...]:
    t = gd.entity(p.name).type
    out: list[tuple[str, Any]] = []
    if t == "underground-belt":
        out.append(("type", p.extra.get("type", "input")))
    if t == "assembling-machine" and p.extra.get("recipe"):
        out.append(("recipe", p.extra["recipe"]))
    return tuple(out)


def placement_key(gd: GameData, p: Placement) -> Key:
    keep_dir = direction_matters(gd, p.name, p.extra.get("recipe"))
    return (
        p.name,
        round(p.position.x, NDIGITS) + 0.0,
        round(p.position.y, NDIGITS) + 0.0,
        int(p.direction) if keep_dir else None,
        _extras(gd, p),
    )


def normalized(layout: Layout, gd: GameData) -> list[Key]:
    """Sorted comparison keys of all placements."""
    return sorted(
        (placement_key(gd, p) for p in layout.placements),
        key=lambda k: (k[0], k[1], k[2], -1 if k[3] is None else k[3], repr(k[4])),
    )


def diff(a: Layout, b: Layout, gd: GameData) -> tuple[list[Key], list[Key]]:
    """(keys only in ``a``, keys only in ``b``), as multisets."""
    ca = Counter(placement_key(gd, p) for p in a.placements)
    cb = Counter(placement_key(gd, p) for p in b.placements)
    only_a = sorted((ca - cb).elements(), key=repr)
    only_b = sorted((cb - ca).elements(), key=repr)
    return only_a, only_b


def same(a: Layout, b: Layout, gd: GameData) -> bool:
    only_a, only_b = diff(a, b, gd)
    return not only_a and not only_b


def describe_diff(a: Layout, b: Layout, gd: GameData, limit: int = 20) -> str:
    only_a, only_b = diff(a, b, gd)
    lines = [f"- {k}" for k in only_a[:limit]] + [f"+ {k}" for k in only_b[:limit]]
    return "\n".join(lines) if lines else "(equal)"
