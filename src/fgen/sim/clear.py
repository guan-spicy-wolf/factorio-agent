"""Remove natural obstacles before a snapshot (plan-phase3a §3.2).

``clear_obstacles`` destroys trees (``tree``), rocks (``simple-entity``), items lying on
the ground (``item-entity``) and corpses (``corpse``) in an area and reports how many of
each it destroyed. It leaves resources, cliffs, water tiles and every player/force
entity alone. Order on a real map is fixed: clear -> snapshot -> generate/route ->
deploy (a snapshot taken before clearing would still show the trees as occupied).
"""

from __future__ import annotations

from typing import Any

from fgen.geom.vec import Tile
from fgen.sim import lua
from fgen.sim.rcon import RconClient
from fgen.terrain.model import Area

CLEARABLE_TYPES = ("tree", "simple-entity", "item-entity", "corpse")


def _box(area: Area) -> str:
    a, b = area
    return lua.area_lit(((a.x, a.y), (b.x + 1, b.y + 1)))


def count_entities(rcon: RconClient, area: Area) -> dict[str, int]:
    """Entities in the (inclusive tile) area by type, characters excluded."""
    out = rcon.lua_json(
        lua.PRELUDE
        + "local n = {}\n"
        + f"for _, e in pairs(S.find_entities_filtered{{area={_box(area)}}}) do\n"
        + "  if e.type ~= 'character' then n[e.type] = (n[e.type] or 0) + 1 end\nend\n"
        + lua.emit("n")
    )
    return {k: int(v) for k, v in (out or {}).items()}


def clear_obstacles(
    rcon: RconClient, area: Area, types: tuple[str, ...] = CLEARABLE_TYPES
) -> dict[str, Any]:
    """Destroy entities of ``types`` in the area. Returns ``{type: count}`` for the
    destroyed ones plus ``total``; only entities without a force other than neutral
    (natural map entities) are touched."""
    out = rcon.lua_json(
        lua.PRELUDE
        + "local n, total = {}, 0\n"
        + f"for _, e in pairs(S.find_entities_filtered{{area={_box(area)}, "
        + f"type={lua.lit(list(types))}}}) do\n"
        + "  if e.valid and (e.force == nil or e.force.name == 'neutral') then\n"
        + "    local t = e.type\n"
        + "    if e.destroy() then n[t] = (n[t] or 0) + 1; total = total + 1 end\n"
        + "  end\nend\n"
        + lua.emit("{counts=n, total=total}")
    )
    counts = {k: int(v) for k, v in (out["counts"] or {}).items()}
    return {**{t: counts.get(t, 0) for t in types}, "total": int(out["total"])}


def entity_tiles(rcon: RconClient, area: Area, types: tuple[str, ...]) -> frozenset[Tile]:
    """Tiles under the bounding boxes of entities of ``types`` (as ``RconTerrain.occupied``
    computes them), clipped to the area."""
    from fgen.sim.terrain import AABB_FN, RLE_FN, _rle_rows
    from fgen.terrain.model import area_tiles

    out = rcon.lua_json(
        lua.PRELUDE
        + RLE_FN
        + AABB_FN
        + "local ps = {}\n"
        + f"for _, e in pairs(S.find_entities_filtered{{area={_box(area)}, "
        + f"type={lua.lit(list(types))}}}) do\n"
        + "  local x0, y0, x1, y1 = aabb(e.bounding_box)\n"
        + "  for x = math.floor(x0), math.ceil(x1) - 1 do\n"
        + "    for y = math.floor(y0), math.ceil(y1) - 1 do\n"
        + "      table.insert(ps, {x=x, y=y})\n"
        + "    end\n  end\nend\n"
        + lua.emit("{rows=rle(ps)}")
    )
    return frozenset(_rle_rows(out["rows"])) & area_tiles(area)
