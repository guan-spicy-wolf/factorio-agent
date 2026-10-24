"""``RconTerrain``: read terrain from a running game, paint ore (plan-phase2 §3.3).

Implements ``fgen.sim.protocol.TerrainQuery`` and exports a
:class:`fgen.terrain.model.Terrain` snapshot. Measured on 2.1.23 (VERIFY.md §9):

- ``LuaTilePrototype.collision_mask`` is ``{layers = {name = true, ...}}`` (2.0+ form;
  no longer a list). Grass/dirt carry only ``ground_tile``; water has ``water_tile``
  (+ item, player, resource, floor, doodad); ``out-of-map`` additionally ``out_of_map``.
- ``find_tiles_filtered{collision_mask = {names...}}`` matches tiles having *any* of the
  layers. A tile blocks building when it shares a layer with the building's own
  ``collision_mask.layers``; :class:`RconTerrain` uses the union over its
  ``reference_entities`` (default: the drill, belt and pole a mining unit uses).
- ``surface.request_to_generate_chunks`` + ``force_generate_chunk_requests()`` in the
  same ``/sc`` generates the chunks synchronously (``is_chunk_generated`` is true
  right after).
- Resource entities: ``find_entities_filtered{type="resource"}``, one per tile at the
  tile centre, ``amount`` per entity.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from fgen.geom.vec import Tile
from fgen.sim import lua
from fgen.sim.rcon import RconClient
from fgen.terrain.model import Area, Terrain, area_tiles

DEFAULT_REFERENCE = ("electric-mining-drill", "transport-belt", "small-electric-pole")
BAND_ROWS = 32  # rows per RCON query (keeps each answer small)


def _lua_area(area: Area) -> lua.Area:
    a, b = area
    return (a.x, a.y), (b.x + 1, b.y + 1)


def _bands(area: Area, rows: int = BAND_ROWS) -> Iterable[Area]:
    a, b = area
    y = a.y
    while y <= b.y:
        yield Tile(a.x, y), Tile(b.x, min(b.y, y + rows - 1))
        y += rows


def _rle_rows(rows: Any) -> set[Tile]:
    """Decode ``{y: [x0, x1, x0, x1, ...]}`` (inclusive runs)."""
    out: set[Tile] = set()
    if not rows:
        return out
    for y, runs in rows.items():
        rs = list(runs.values()) if isinstance(runs, dict) else list(runs)
        for i in range(0, len(rs), 2):
            for x in range(int(rs[i]), int(rs[i + 1]) + 1):
                out.add(Tile(x, int(y)))
    return out


RLE_FN = """
local function rle(tiles)
  local rows = {}
  for _, p in pairs(tiles) do
    local y = math.floor(p.y)
    rows[y] = rows[y] or {}
    table.insert(rows[y], math.floor(p.x))
  end
  local out = {}
  for y, xs in pairs(rows) do
    table.sort(xs)
    local runs, s, e = {}, nil, nil
    for _, x in ipairs(xs) do
      if s == nil then s, e = x, x
      elseif x == e + 1 then e = x
      elseif x ~= e then table.insert(runs, s); table.insert(runs, e); s, e = x, x end
    end
    if s ~= nil then table.insert(runs, s); table.insert(runs, e) end
    out[tostring(y)] = runs
  end
  return out
end
"""


class RconTerrain:
    """Terrain queries over RCON. ``reference_entities`` define what "buildable"
    means (no tile layer shared with any of their collision masks)."""

    def __init__(
        self,
        rcon: RconClient,
        reference_entities: Iterable[str] = DEFAULT_REFERENCE,
    ) -> None:
        self.rcon = rcon
        self.reference = tuple(reference_entities)

    # -- helpers ------------------------------------------------------------------
    def _layers_lua(self) -> str:
        return (
            f"local refs = {lua.lit(list(self.reference))}\n"
            "local L, layers = {}, {}\n"
            "for _, n in ipairs(refs) do\n"
            "  for k in pairs(prototypes.entity[n].collision_mask.layers) do L[k] = true end\n"
            "end\n"
            "for k in pairs(L) do table.insert(layers, k) end\n"
        )

    def generate(self, area: Area) -> None:
        """Generate every chunk the area touches (synchronous)."""
        a, b = area
        cx, cy = (a.x + b.x + 1) / 2, (a.y + b.y + 1) / 2
        radius = int(max(b.x - a.x + 1, b.y - a.y + 1) / 2 // 32) + 1
        self.rcon.lua_json(lua.generate_chunks((cx, cy), radius))

    # -- TerrainQuery ---------------------------------------------------------------
    def resources(self, area: Area) -> dict[Tile, tuple[str, int]]:
        out: dict[Tile, tuple[str, int]] = {}
        for band in _bands(area):
            res = self.rcon.lua_json(
                lua.PRELUDE
                + "local names, idx, rows = {}, {}, {}\n"
                + f"local A = {lua.area_lit(_lua_area(band))}\n"
                + "for _, e in pairs(S.find_entities_filtered{area=A, type='resource'}) do\n"
                + "  local k = idx[e.name]\n"
                + "  if not k then table.insert(names, e.name); k = #names; idx[e.name] = k end\n"
                + "  table.insert(rows, {math.floor(e.position.x), math.floor(e.position.y), k, "
                + "e.amount})\n"
                + "end\n"
                + lua.emit("{names=names, rows=rows}")
            )
            names = _as_list(res["names"])
            for x, y, k, amount in _as_list(res["rows"]):
                out[Tile(int(x), int(y))] = (names[int(k) - 1], int(amount))
        return out

    def blocked(self, area: Area) -> frozenset[Tile]:
        """Tiles whose collision layers clash with a reference entity."""
        out: set[Tile] = set()
        for band in _bands(area):
            res = self.rcon.lua_json(
                lua.PRELUDE
                + RLE_FN
                + self._layers_lua()
                + f"local ts = S.find_tiles_filtered{{area={lua.area_lit(_lua_area(band))}, "
                + "collision_mask=layers}\n"
                + "local ps = {}\nfor _, t in pairs(ts) do table.insert(ps, t.position) end\n"
                + lua.emit("{rows=rle(ps)}")
            )
            out |= _rle_rows(res["rows"])
        return frozenset(out)

    def water(self, area: Area) -> frozenset[Tile]:
        out: set[Tile] = set()
        for band in _bands(area):
            res = self.rcon.lua_json(
                lua.PRELUDE
                + RLE_FN
                + f"local ts = S.find_tiles_filtered{{area={lua.area_lit(_lua_area(band))}, "
                + "collision_mask={'water_tile'}}\n"
                + "local ps = {}\nfor _, t in pairs(ts) do\n"
                + "  if not t.prototype.collision_mask.layers.out_of_map then "
                + "table.insert(ps, t.position) end\nend\n"
                + lua.emit("{rows=rle(ps)}")
            )
            out |= _rle_rows(res["rows"])
        return frozenset(out)

    def buildable(self, tile: Tile) -> bool:
        return tile not in self.blocked((tile, tile)) and tile not in self.occupied((tile, tile))

    def occupied(self, area: Area) -> frozenset[Tile]:
        """Tiles under the collision boxes of non-resource entities (trees, rocks,
        cliffs, buildings), characters excluded; clipped to ``area``."""
        out: set[Tile] = set()
        for band in _bands(area):
            res = self.rcon.lua_json(
                lua.PRELUDE
                + RLE_FN
                + f"local A = {lua.area_lit(_lua_area(band))}\n"
                + "local ps = {}\n"
                + "for _, e in pairs(S.find_entities_filtered{area=A}) do\n"
                + "  if e.type ~= 'resource' and e.type ~= 'character' then\n"
                + "    local bb = e.bounding_box\n"
                + "    for x = math.floor(bb.left_top.x), math.ceil(bb.right_bottom.x) - 1 do\n"
                + "      for y = math.floor(bb.left_top.y), math.ceil(bb.right_bottom.y) - 1 do\n"
                + "        table.insert(ps, {x=x, y=y})\n"
                + "      end\n    end\n  end\nend\n"
                + lua.emit("{rows=rle(ps)}")
            )
            out |= _rle_rows(res["rows"])
        return frozenset(out) & area_tiles(area)

    def snapshot(self, area: Area, *, generate: bool = True) -> Terrain:
        if generate:
            self.generate(area)
        blocked = self.blocked(area)
        return Terrain(
            buildable=area_tiles(area) - blocked,
            resources=self.resources(area),
            water=self.water(area),
            occupied=self.occupied(area),
        )

    # -- painting -----------------------------------------------------------------
    def clear_resources(self, area: Area) -> int:
        out = self.rcon.lua_json(
            lua.PRELUDE
            + "local n = 0\n"
            + f"for _, e in pairs(S.find_entities_filtered{{area={lua.area_lit(_lua_area(area))}, "
            + "type='resource'}) do e.destroy(); n = n + 1 end\n"
            + lua.emit("{destroyed=n}")
        )
        return int(out["destroyed"])

    def paint(self, resources: Mapping[Tile, tuple[str, int]], *, chunk: int = 2000) -> int:
        """Create one resource entity per tile (at the tile centre). Existing resources
        on those tiles are removed first. Returns the number created."""
        items = sorted(resources.items())
        created = 0
        for i in range(0, len(items), chunk):
            part = items[i : i + chunk]
            names = sorted({n for _, (n, _) in part})
            idx = {n: k + 1 for k, n in enumerate(names)}
            rows = [[t.x, t.y, idx[n], int(a)] for t, (n, a) in part]
            out = self.rcon.lua_json(
                lua.PRELUDE
                + f"local names = {lua.lit(names)}\n"
                + f"local rows = {lua.lit(rows)}\n"
                + "local n, failed = 0, 0\n"
                + "for _, r in ipairs(rows) do\n"
                + "  local pos = {r[1] + 0.5, r[2] + 0.5}\n"
                + "  for _, e in pairs(S.find_entities_filtered{position=pos, type='resource'})"
                + " do e.destroy() end\n"
                + "  local e = S.create_entity{name=names[r[3]], position=pos, amount=r[4]}\n"
                + "  if e then n = n + 1 else failed = failed + 1 end\n"
                + "end\n"
                + lua.emit("{created=n, failed=failed}")
            )
            if int(out["failed"]):
                raise RuntimeError(f"{out['failed']} resource entities could not be created")
            created += int(out["created"])
        return created

    def set_tiles(self, tiles: Iterable[Tile], name: str) -> None:
        """Replace map tiles (e.g. make a test lake with ``name='water'``)."""
        ts = [{"name": name, "position": [t.x, t.y]} for t in tiles]
        self.rcon.lua(lua.PRELUDE + f"S.set_tiles({lua.lit(ts)})")


def _as_list(x: Any) -> list[Any]:
    if isinstance(x, dict):
        return [] if not x else [x[k] for k in sorted(x, key=int)]
    return list(x or [])
