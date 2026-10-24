"""Lua snippet templates sent with ``/sc``.

Each snippet is self-contained (console commands share no locals) and reports back with
``rcon.print(helpers.table_to_json(...))`` as its last line. Only names and ``defines``
appear here; no game numbers.

Areas are ``((x0, y0), (x1, y1))`` in world coordinates, x east and y south.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

type Area = tuple[tuple[float, float], tuple[float, float]]

PRELUDE = "local S = game.surfaces[1]; local F = game.forces.player\n"

# Tile collision layers that make a tile unusable for ordinary buildings.
BLOCKING_TILE_LAYERS = ("water_tile", "out_of_map", "object", "player", "item", "resource")


def lit(v: Any) -> str:
    """Python value -> Lua literal (numbers, strings, bools, None, lists, dicts)."""
    if v is None:
        return "nil"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int | float):
        return repr(float(v)) if isinstance(v, float) else str(v)
    if isinstance(v, str):
        return json.dumps(v)  # JSON string escapes are valid Lua escapes for our inputs
    if isinstance(v, dict):
        return "{" + ", ".join(f"[{lit(k)}] = {lit(x)}" for k, x in v.items()) + "}"
    if isinstance(v, Iterable):
        return "{" + ", ".join(lit(x) for x in v) + "}"
    raise TypeError(f"cannot express {type(v).__name__} in Lua")


def area_lit(a: Area) -> str:
    (x0, y0), (x1, y1) = a
    return f"{{{{{float(x0)!r}, {float(y0)!r}}}, {{{float(x1)!r}, {float(y1)!r}}}}}"


def emit(expr: str) -> str:
    return f"rcon.print(helpers.table_to_json({expr}))"


def generate_chunks(center: tuple[float, float], radius_chunks: int) -> str:
    return (
        PRELUDE
        + f"S.request_to_generate_chunks({lit(list(center))}, {int(radius_chunks)})\n"
        + "S.force_generate_chunk_requests()\n"
        + emit("{ok=true}")
    )


def terrain_report(a: Area) -> str:
    """Tile counts by name, number of blocking tiles and of entities in the area."""
    return (
        PRELUDE
        + f"local A = {area_lit(a)}\n"
        + "local names = {}\n"
        + "for _, t in pairs(S.find_tiles_filtered{area=A}) do "
        + "names[t.name] = (names[t.name] or 0) + 1 end\n"
        + "local ents = {}\n"
        + "for _, e in pairs(S.find_entities_filtered{area=A}) do "
        + "ents[e.name] = (ents[e.name] or 0) + 1 end\n"
        + "local blocked = S.count_tiles_filtered{area=A, "
        + f"collision_mask={lit(BLOCKING_TILE_LAYERS)}}}\n"
        + "local total = S.count_tiles_filtered{area=A}\n"
        + emit("{tiles=names, total=total, blocked=blocked, entities=ents}")
    )


def clear_area(a: Area) -> str:
    """Destroy every entity in the area except characters; returns how many."""
    return (
        PRELUDE
        + f"local n = 0\nfor _, e in pairs(S.find_entities_filtered{{area={area_lit(a)}}}) do\n"
        + "  if e.valid and e.type ~= 'character' then e.destroy(); n = n + 1 end\nend\n"
        + emit("{destroyed=n}")
    )


READ_ENTITY_FN = """
local function describe(e)
  local d = {name=e.name, type=e.type, position=e.position, direction=e.direction,
             unit_number=e.unit_number}
  if e.type == "underground-belt" then d.belt_to_ground_type = e.belt_to_ground_type end
  if e.type == "loader" or e.type == "loader-1x1" then d.loader_type = e.loader_type end
  if e.type == "assembling-machine" then
    local r = e.get_recipe(); d.recipe = r and r.name or nil
  end
  if e.type == "electric-pole" then
    local ns = {}
    local c = e.get_wire_connector(defines.wire_connector_id.pole_copper, false)
    if c then
      for _, con in pairs(c.connections) do
        local o = con.target.owner
        if o.type == "electric-pole" then table.insert(ns, o.unit_number) end
      end
    end
    d.copper_neighbours = ns
  end
  d.electric_network_id = e.electric_network_id
  return d
end
"""


def read_entities(a: Area) -> str:
    return (
        PRELUDE
        + READ_ENTITY_FN
        + "local out = {}\n"
        + f"for _, e in pairs(S.find_entities_filtered{{area={area_lit(a)}}}) do\n"
        + "  if e.type ~= 'character' then table.insert(out, describe(e)) end\nend\n"
        + emit("out")
    )


def build_blueprint(bp_string: str, position: tuple[float, float], revive: bool = True) -> str:
    """Import a blueprint string into a script inventory, build it as ghosts at
    ``position`` (force_build) and optionally revive every ghost.

    Reports ``import`` (0 ok, -1 ok with errors, 1 failed), the ghosts' inner names and
    positions, and the number revived.
    """
    return (
        PRELUDE
        + "local inv = game.create_inventory(1); local st = inv[1]\n"
        + f"local r = st.import_stack({lit(bp_string)})\n"
        + "local out = {import=r, ghosts={}, revived=0, failed={}}\n"
        + "if r ~= 1 and st.is_blueprint_setup() then\n"
        + f"  local gs = st.build_blueprint{{surface=S, force=F, position={lit(list(position))}, "
        + "force_build=true, raise_built=false}\n"
        + "  for _, g in pairs(gs) do\n"
        + "    table.insert(out.ghosts, {name=g.ghost_name, position=g.position})\n"
        + "  end\n"
        + ("  for _, g in pairs(gs) do\n" if revive else "  for _, g in pairs({}) do\n")
        + "    if g.valid then\n"
        + "      local name, pos = g.ghost_name, g.position\n"
        + "      local _, ent = g.revive{raise_revive=false}\n"
        + "      if ent then out.revived = out.revived + 1 else "
        + "table.insert(out.failed, {name=name, position=pos}) end\n"
        + "    end\n"
        + "  end\n"
        + "end\n"
        + "inv.destroy()\n"
        + emit("out")
    )


def destroy_ghosts(a: Area) -> str:
    return (
        PRELUDE
        + f"local n = 0\nfor _, g in pairs(S.find_entities_filtered{{area={area_lit(a)}, "
        + "type='entity-ghost'}) do g.destroy(); n = n + 1 end\n"
        + emit("{destroyed=n}")
    )


def create_entities(specs: list[dict[str, Any]]) -> str:
    """``surface.create_entity`` for each spec (a create_entity parameter table); reports
    which failed."""
    return (
        PRELUDE
        + f"local specs = {lit(specs)}\n"
        + "local out = {created=0, failed={}}\n"
        + "for i, sp in ipairs(specs) do\n"
        + "  sp.force = sp.force or F\n"
        + "  local e = S.create_entity(sp)\n"
        + "  if e then out.created = out.created + 1 else table.insert(out.failed, i) end\n"
        + "end\n"
        + emit("out")
    )
