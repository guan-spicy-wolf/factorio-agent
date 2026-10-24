"""In-game checks for the ``docs/VERIFY.md`` items that need a running Factorio.

Each check builds a small rig in its own band of the flat test field, runs it for a
number of **game ticks** (polled through ``game.tick`` or sampled by an in-game
``script.on_nth_tick`` handler), and returns a JSON-able dict::

    {"id": 1, "item": "...", "assumption": "...", "holds": True | False | None,
     "conclusion": "...", "data": {...}}

``holds`` says whether the code's current assumption matched the game; the checks
report, they do not edit anything. ``uv run fgen verify`` prints them as JSON lines.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

from fgen.blueprint import codec
from fgen.blueprint.export import layout_to_blueprint
from fgen.data.schema import GameData
from fgen.gen import throughput as tp
from fgen.geom import belt as beltgeom
from fgen.geom import inserter as insgeom
from fgen.geom import pole
from fgen.geom.vec import CARDINALS, Direction, Tile, Vec2
from fgen.layout.model import Layout, Placement
from fgen.sim import lua
from fgen.sim.harness import LOADER_FOR_BELT
from fgen.sim.server import HeadlessServer

type Result = dict[str, Any]

BAND = 20  # tiles of field height reserved per check
X0, Y0 = -90, -90  # top-left of the first band (inside the verified 200x200 field)

# Prototype names used by the rigs (names only; every number comes from GameData).
SOURCE_CHEST = "infinity-chest"
SINK_CHEST = "steel-chest"
POWER_SOURCE = "electric-energy-interface"
RIG_POLE = "small-electric-pole"
LOADERS = LOADER_FOR_BELT


def band_origin(check_id: int) -> Tile:
    return Tile(X0, Y0 + BAND * (check_id - 1))


def band_area(check_id: int) -> lua.Area:
    o = band_origin(check_id)
    return (o.x - 1, o.y - 1), (o.x + 181, o.y + BAND - 1)


def _clear(srv: HeadlessServer, area: lua.Area) -> None:
    srv.rcon.lua("script.on_nth_tick(nil)")
    srv.rcon.lua_json(lua.clear_area(area))


def _dir_name(d: int) -> str:
    return Direction(d).name


POWER_FN = f"""
local function power(x, y)
  S.create_entity{{name={lua.lit(POWER_SOURCE)}, position={{x + 1, y + 1}}, force=F}}
  return S.create_entity{{name={lua.lit(RIG_POLE)}, position={{x + 2.5, y - 0.5}}, force=F}}
end
"""


# ---- 1. inserter direction -----------------------------------------------------------


def check_inserter_direction(srv: HeadlessServer, gd: GameData) -> Result:
    """Chests on both sides of an inserter: iron plates on the side our geometry calls
    "pickup", copper plates on the "drop" side. After 120 ticks see what moved."""
    o = band_origin(1)
    rigs = []
    for k, d in enumerate(CARDINALS):
        c = Tile(o.x + 2 + 8 * k, o.y + 4).center()
        pu = insgeom.pickup_tile(gd, "inserter", c, d)
        dr = insgeom.drop_tile(gd, "inserter", c, d)
        rigs.append(
            {
                "d": int(d),
                "c": [c.x, c.y],
                "pu": [pu.x + 0.5, pu.y + 0.5],
                "dr": [dr.x + 0.5, dr.y + 0.5],
                "px": o.x + 8 * k,
                "py": o.y + 8,
            }
        )
    _clear(srv, band_area(1))
    code = (
        lua.PRELUDE
        + POWER_FN
        + f"local rigs = {lua.lit(rigs)}\n"
        + "storage.fgen_rigs = {}\n"
        + "for i, r in ipairs(rigs) do\n"
        + "  local a = S.create_entity{name='iron-chest', position=r.pu, force=F}\n"
        + "  local b = S.create_entity{name='iron-chest', position=r.dr, force=F}\n"
        + "  a.insert{name='iron-plate', count=50}; b.insert{name='copper-plate', count=50}\n"
        + "  local ins = S.create_entity{name='inserter', position=r.c, direction=r.d, force=F}\n"
        + "  power(r.px, r.py)\n"
        + "  local pole = S.create_entity{name='small-electric-pole', "
        + "position={r.c[1] + 1, r.c[2] + 1}, force=F}\n"
        + "  storage.fgen_rigs[i] = {a=a, b=b, ins=ins}\n"
        + "end\n"
        + "local out = {}\n"
        + "for i, r in ipairs(storage.fgen_rigs) do out[i] = {dir=r.ins.direction, "
        + "pickup=r.ins.pickup_position, drop=r.ins.drop_position, "
        + "net=r.ins.electric_network_id} end\n"
        + lua.emit("out")
    )
    before = srv.rcon.lua_json(code)
    srv.wait_ticks(120)
    after = srv.rcon.lua_json(
        "local out = {}\n"
        "for i, r in ipairs(storage.fgen_rigs) do\n"
        "  local ai = r.a.get_inventory(defines.inventory.chest)\n"
        "  local bi = r.b.get_inventory(defines.inventory.chest)\n"
        "  out[i] = {pickup_side={iron=ai.get_item_count('iron-plate'), "
        "copper=ai.get_item_count('copper-plate')}, drop_side={iron=bi.get_item_count("
        "'iron-plate'), copper=bi.get_item_count('copper-plate')}}\n"
        "end\n" + lua.emit("out")
    )
    per_dir = {}
    holds = True
    for rig, b, a in zip(rigs, before, after, strict=True):
        moved_forward = a["drop_side"]["iron"] > 0 and a["pickup_side"]["copper"] == 0
        api_pu = Vec2(b["pickup"]["x"], b["pickup"]["y"]).floor()
        api_dr = Vec2(b["drop"]["x"], b["drop"]["y"]).floor()
        geom_ok = api_pu == Vec2(*rig["pu"]).floor() and api_dr == Vec2(*rig["dr"]).floor()
        holds = holds and moved_forward and geom_ok and b["dir"] == rig["d"]
        per_dir[_dir_name(rig["d"])] = {
            "items_moved_pickup_to_drop": a["drop_side"]["iron"],
            "items_moved_drop_to_pickup": a["pickup_side"]["copper"],
            "api_pickup_tile": [api_pu.x, api_pu.y],
            "api_drop_tile": [api_dr.x, api_dr.y],
            "geom_matches_api": geom_ok,
            "powered": b["net"] is not None,
        }
    concl = (
        "direction=N picks up from the north tile and drops to the south tile; E/S/W are "
        "the clockwise rotations (E picks from the east). Matches geom/inserter.py."
        if holds
        else "inserter direction semantics differ from geom/inserter.py; see data"
    )
    return {
        "id": 1,
        "item": "inserter direction",
        "assumption": "N-facing (direction=0) inserter picks up north, drops south",
        "holds": holds,
        "conclusion": concl,
        "data": per_dir,
    }


# ---- 2. poles in a blueprint without wires -------------------------------------------


def _two_pole_layout(gd: GameData) -> Layout:
    """Two small poles, as far apart as their wire reach allows (whole tiles)."""
    reach = gd.entity(RIG_POLE).maximum_wire_distance or 0
    gap = math.floor(reach)
    return Layout([Placement(RIG_POLE, Vec2(0.5, 0.5)), Placement(RIG_POLE, Vec2(0.5 + gap, 0.5))])


def check_pole_autoconnect(srv: HeadlessServer, gd: GameData) -> Result:
    from fgen.sim.deployer import RconDeployer

    o = band_origin(2)
    layout = _two_pole_layout(gd)
    dep = RconDeployer(srv.rcon, gd)
    data: dict[str, Any] = {"pole_gap_tiles": layout.placements[1].position.x - 0.5}
    for k, with_wires in enumerate((False, True)):
        origin = Tile(o.x + 2, o.y + 2 + 4 * k)
        bp = layout_to_blueprint(layout, gd)
        if not with_wires:
            bp["blueprint"].pop("wires", None)
        out = srv.rcon.lua_json(
            lua.build_blueprint(
                codec.encode(bp), (origin.x + 1, origin.y), (origin.x + 0.5, origin.y + 0.5)
            )
        )
        ents = dep.read_entities(((origin.x - 1, origin.y - 1), (origin.x + 40, origin.y + 2)))
        nets = sorted({e.get("electric_network_id") for e in ents})
        data["with_wires" if with_wires else "without_wires"] = {
            "revived": out["revived"],
            "networks": len(nets),
            "wires_in_blueprint": bp["blueprint"].get("wires", []),
        }
    autoconnect = data["without_wires"]["networks"] == 1
    exporter_ok = data["with_wires"]["networks"] == 1
    return {
        "id": 2,
        "item": "pole auto-connect when a blueprint has no wires",
        "assumption": "poles built from a blueprint without `wires` connect automatically",
        "holds": autoconnect,
        "conclusion": (
            (
                "poles DO auto-connect"
                if autoconnect
                else "poles built from a blueprint without `wires` stay unconnected (separate "
                "networks); the exporter must write `wires`"
            )
            + (
                "; with the exporter's `wires` they form one network"
                if exporter_ok
                else "; even with `wires` they are not connected"
            )
        ),
        "exporter_writes_wires_ok": exporter_ok,
        "data": data,
    }


# ---- 3. underground belts ----------------------------------------------------------


def check_underground(srv: HeadlessServer, gd: GameData) -> Result:
    o = band_origin(3)
    name = "underground-belt"
    reach = beltgeom.underground_partner_range(gd, name)
    y = o.y + 2.5
    code = (
        lua.PRELUDE
        + f"local x0, y = {o.x + 2.5}, {y}\n"
        + "S.create_entity{name='transport-belt', position={x0 - 1, y}, "
        + "direction=defines.direction.east, force=F}\n"
        + f"S.create_entity{{name={lua.lit(name)}, position={{x0, y}}, "
        + "direction=defines.direction.east, type='input', force=F}\n"
        + f"S.create_entity{{name={lua.lit(name)}, position={{x0 + 3, y}}, "
        + "direction=defines.direction.east, type='output', force=F}\n"
        + "S.create_entity{name='transport-belt', position={x0 + 4, y}, "
        + "direction=defines.direction.east, force=F}\n"
        + lua.emit("{ok=true}")
    )
    srv.rcon.lua_json(code)
    exp = srv.rcon.lua_json(lua.export_blueprint(((o.x, o.y + 2), (o.x + 8, o.y + 3))))
    bp = codec.decode(exp["string"])
    ents = [
        {k: e.get(k) for k in ("name", "position", "direction", "type")}
        for e in bp["blueprint"]["entities"]
    ]
    ugs = [e for e in ents if e["name"] == name]
    types = sorted(str(e.get("type")) for e in ugs)
    dirs = sorted({e.get("direction") for e in ugs})
    flow = Direction.E.to_blueprint()
    format_ok = types == ["input", "output"] and dirs == [flow]
    # pairing distance: input at x, output at x + k for k = 1 .. reach + 2
    pair_code = (
        lua.PRELUDE
        + f"local res = {{}}\nfor k = 1, {reach + 2} do\n"
        + f"  local x, y = {o.x + 2.5}, {o.y + 6.5} + k\n"
        + f"  local a = S.create_entity{{name={lua.lit(name)}, position={{x, y}}, "
        + "direction=defines.direction.east, type='input', force=F}\n"
        + f"  local b = S.create_entity{{name={lua.lit(name)}, position={{x + k, y}}, "
        + "direction=defines.direction.east, type='output', force=F}\n"
        + "  local nb = a.underground_belt_neighbour\n"
        + "  res[k] = (nb ~= nil and nb.unit_number == b.unit_number)\n"
        + "end\n"
        + lua.emit("res")
    )
    paired = srv.rcon.lua_json(pair_code)
    paired = paired if isinstance(paired, list) else [paired[str(k)] for k in range(1, reach + 3)]
    max_k = max((k for k, ok in enumerate(paired, start=1) if ok), default=0)
    range_ok = max_k == reach
    return {
        "id": 3,
        "item": "underground belt blueprint format and pairing distance",
        "assumption": (
            "blueprint `type` is input/output, both directions equal the flow; partner "
            f"within 1..max_distance tiles ({name}: {reach})"
        ),
        "holds": format_ok and range_ok,
        "conclusion": (
            f"game-exported pair: types {types}, directions {dirs} (flow east = {flow}); "
            f"{name} pairs up to an offset of {max_k} tiles (max_distance {reach})"
        ),
        "data": {"exported_entities": ents, "paired_by_offset": paired},
    }


# ---- 4. belt lanes ---------------------------------------------------------------


def check_belt_lanes(srv: HeadlessServer, gd: GameData) -> Result:
    """East-flowing belts. (a) side-load from the north with a south-flowing belt; (b) an
    inserter north of the belt drops onto it; (c) one south of it. Read which
    ``get_transport_line`` index (``defines.transport_line.left_line`` = 1) holds the
    items."""
    o = band_origin(4)
    code = (
        lua.PRELUDE
        + POWER_FN
        + f"local x0, y0 = {o.x + 2.5}, {o.y + 3.5}\n"
        + """
local E, Sd, N = defines.direction.east, defines.direction.south, defines.direction.north
local rigs = {}
-- (a) side-load: feeder flows south into the main belt's 3rd tile
local main = {}
for i = 0, 7 do
  main[i] = S.create_entity{name='transport-belt', position={x0 + i, y0}, direction=E, force=F}
end
local feed = {}
for j = 1, 3 do
  feed[j] = S.create_entity{name='transport-belt', position={x0 + 2, y0 - j}, direction=Sd,
                             force=F}
end
for _, line in pairs({1, 2}) do
  feed[3].get_transport_line(line).insert_at_back({name='iron-plate', count=1})
  feed[2].get_transport_line(line).insert_at_back({name='iron-plate', count=1})
end
-- (b) inserter north of a belt, (c) inserter south of a belt
local function ins_rig(y, from_north)
  local belts = {}
  for i = 0, 3 do
    belts[i] = S.create_entity{name='transport-belt', position={x0 + i, y}, direction=E,
                                force=F}
  end
  local dy = from_north and -1 or 1
  local d = from_north and N or Sd   -- picks up on the far side from the belt
  S.create_entity{name='inserter', position={x0 + 3, y + dy}, direction=d, force=F}
  local c = S.create_entity{name='iron-chest', position={x0 + 3, y + 2 * dy}, force=F}
  c.insert{name='copper-plate', count=5}
  S.create_entity{name='medium-electric-pole', position={x0 + 4, y + dy}, force=F}
  return belts
end
storage.fgen_lanes = {main=main, north=ins_rig(y0 + 6, true), south=ins_rig(y0 + 12, false)}
power(x0 + 6, y0 + 9)
"""
        + lua.emit("{ok=true}")
    )
    srv.rcon.lua_json(code)
    srv.wait_ticks(240)
    out = srv.rcon.lua_json(
        """
local function lanes(belts)
  local l1, l2 = 0, 0
  for _, b in pairs(belts) do
    l1 = l1 + b.get_transport_line(defines.transport_line.left_line).get_item_count()
    l2 = l2 + b.get_transport_line(defines.transport_line.right_line).get_item_count()
  end
  return {left_line=l1, right_line=l2}
end
local t = storage.fgen_lanes
"""
        + lua.emit(
            "{sideload_from_north=lanes(t.main), inserter_from_north=lanes(t.north), "
            "inserter_from_south=lanes(t.south)}"
        )
    )

    def which(d: dict[str, int]) -> str | None:
        if d["left_line"] and not d["right_line"]:
            return "left"
        if d["right_line"] and not d["left_line"]:
            return "right"
        return None

    flow = Direction.E
    north_lane_name = "left" if beltgeom.lane_side(flow, "left") == Direction.N else "right"
    side = which(out["sideload_from_north"])
    ins_n = which(out["inserter_from_north"])
    ins_s = which(out["inserter_from_south"])
    exp_n = beltgeom.drop_lane(flow, Direction.S)  # arm moves south onto the belt
    exp_s = beltgeom.drop_lane(flow, Direction.N)
    holds = side == north_lane_name and ins_n == exp_n and ins_s == exp_s
    return {
        "id": 4,
        "item": "belt lane naming",
        "assumption": (
            "lanes named relative to flow, left = north side of an east belt = "
            "defines.transport_line.left_line (1); side-loading fills the near lane; "
            "inserters drop on the far lane"
        ),
        "holds": holds,
        "conclusion": (
            f"side-load from the north lands on the {side} line (geom says north = "
            f"{north_lane_name}); inserter north of the belt drops on {ins_n} "
            f"(geom: {exp_n}), south of it on {ins_s} (geom: {exp_s})"
        ),
        "data": out,
    }


# ---- 5. blueprint fields the exporter writes ---------------------------------------------


def check_blueprint_format(srv: HeadlessServer, gd: GameData) -> Result:
    o = band_origin(5)
    layout = Layout(
        [
            Placement("transport-belt", Vec2(0.5, 0.5), Direction.N),
            Placement("inserter", Vec2(1.5, 0.5), Direction.N),
            Placement("iron-chest", Vec2(2.5, 0.5), Direction.N),
        ]
    )
    ours = layout_to_blueprint(layout, gd, label="fgen-verify", icon="iron-chest")
    variants = {"ours_direction0_icon_type": ours}
    bare = codec.decode(codec.encode(ours))
    for e in bare["blueprint"]["entities"]:
        e.pop("direction", None)
    for ic in bare["blueprint"]["icons"]:
        ic["signal"].pop("type", None)
    variants["no_direction_icon_without_type"] = bare
    results: dict[str, Any] = {}
    for k, (key, bp) in enumerate(variants.items()):
        pos = (o.x + 2 + 6 * k, o.y + 2)
        out = srv.rcon.lua_json(
            lua.build_blueprint(codec.encode(bp), pos, (pos[0] + 0.5, pos[1] + 0.5))
        )
        results[key] = {"import_stack": out["import"], "revived": out["revived"]}
    exp = srv.rcon.lua_json(lua.export_blueprint(((o.x + 1, o.y + 1), (o.x + 6, o.y + 4))))
    game_bp = codec.decode(exp["string"])["blueprint"]
    from fgen.blueprint.codec import pack_version, unpack_version

    game_version = unpack_version(int(game_bp["version"]))
    results["game_export"] = {
        "version_unpacked": list(game_version),
        "entities": game_bp.get("entities"),
        "icons": game_bp.get("icons"),
        "keys": sorted(game_bp),
    }
    ok = all(
        r["import_stack"] == 0 and r["revived"] == 3
        for k, r in results.items()
        if k != "game_export"
    )
    version_ok = game_version[:3] == unpack_version(pack_version(gd.version))[:3]
    omits_n = all("direction" not in e for e in game_bp.get("entities", []))
    return {
        "id": 5,
        "item": "explicit direction=0 and icon signal type",
        "assumption": "the game accepts direction 0 written out and icons with `type`",
        "holds": ok,
        "conclusion": (
            (
                "both variants import with result 0 and build completely"
                if ok
                else "an exporter variant was rejected; see data"
            )
            + f"; the game itself {'omits' if omits_n else 'writes'} direction for N "
            f"entities; its version field unpacks to {list(game_version)}"
            + (" (major.minor.patch match our packing)" if version_ok else " (MISMATCH)")
        ),
        "data": results,
    }


# ---- 6. inserter hand size ------------------------------------------------------------


INSERTERS = (
    "burner-inserter",
    "inserter",
    "long-handed-inserter",
    "fast-inserter",
    "bulk-inserter",
)


def check_hand_size(srv: HeadlessServer, gd: GameData, window_ticks: int = 1200) -> Result:
    """Chest -> chest for each inserter on a fresh force (no research). An on_nth_tick
    handler records the largest held stack; items moved are counted over a fixed tick
    window after one warm-up revolution."""
    o = band_origin(6)
    names = [n for n in INSERTERS if n in gd.entities]
    rigs = []
    for k, n in enumerate(names):
        c = Tile(o.x + 2 + 6 * k, o.y + 6).center()
        pu = insgeom.pickup_tile(gd, n, c, Direction.N)
        dr = insgeom.drop_tile(gd, n, c, Direction.N)
        rigs.append(
            {
                "name": n,
                "c": [c.x, c.y],
                "pu": [pu.x + 0.5, pu.y + 0.5],
                "dr": [dr.x + 0.5, dr.y + 0.5],
                "px": o.x + 6 * k,
                "py": o.y + 12,
                "burner": gd.entity(n).is_burner,
            }
        )
    setup = (
        lua.PRELUDE
        + POWER_FN
        + f"local rigs = {lua.lit(rigs)}\n"
        + """
storage.fgen_hand = {}
for i, r in ipairs(rigs) do
  local a = S.create_entity{name='infinity-chest', position=r.pu, force=F}
  a.infinity_container_filters = {{index=1, name='iron-plate', count=200, mode='exactly'}}
  local b = S.create_entity{name='steel-chest', position=r.dr, force=F}
  local ins = S.create_entity{name=r.name, position=r.c, direction=defines.direction.north,
                              force=F}
  if r.burner then ins.get_fuel_inventory().insert{name='coal', count=50} end
  power(r.px, r.py)
  S.create_entity{name='medium-electric-pole', position={r.c[1] + 1, r.c[2]}, force=F}
  storage.fgen_hand[i] = {name=r.name, ins=ins, sink=b, max_hand=0}
end
script.on_nth_tick(1, function()
  for _, r in pairs(storage.fgen_hand) do
    local h = r.ins.held_stack
    if h.valid_for_read and h.count > r.max_hand then r.max_hand = h.count end
  end
end)
"""
        + lua.emit("{ok=true}")
    )
    srv.rcon.lua_json(setup)
    read = (
        "local out = {tick=game.tick, rigs={}}\n"
        "for i, r in ipairs(storage.fgen_hand) do out.rigs[i] = {name=r.name, "
        "moved=r.sink.get_item_count('iron-plate'), max_hand=r.max_hand, "
        "stack_bonus=r.ins.force.inserter_stack_size_bonus, "
        "bulk_bonus=r.ins.force.bulk_inserter_capacity_bonus} end\n" + lua.emit("out")
    )
    srv.wait_ticks(int(gd.constant("ticks_per_second")) * 2)
    a = srv.rcon.lua_json(read)
    srv.wait_ticks(window_ticks)
    b = srv.rcon.lua_json(read)
    srv.rcon.lua("script.on_nth_tick(nil)")
    dt = (b["tick"] - a["tick"]) / gd.constant("ticks_per_second")
    base_hand = gd.constant("inserter_base_hand_size")
    data = {}
    holds = True
    for ra, rb in zip(a["rigs"], b["rigs"], strict=True):
        n = ra["name"]
        rate = (rb["moved"] - ra["moved"]) / dt
        data[n] = {
            "max_hand": rb["max_hand"],
            "items_per_s": round(rate, 4),
            "estimate_per_s": round(tp.inserter_throughput_estimate(gd, n), 4),
            "force_inserter_stack_size_bonus": rb["stack_bonus"],
            "force_bulk_inserter_capacity_bonus": rb["bulk_bonus"],
        }
        holds = holds and rb["max_hand"] == base_hand
    hands = ", ".join(f"{n} {d['max_hand']}" for n, d in data.items())
    return {
        "id": 6,
        "item": "inserter base hand size (no research)",
        "assumption": f"every inserter moves {base_hand:g} item per swing without research",
        "holds": holds,
        "conclusion": f"largest held stack chest-to-chest: {hands}",
        "data": {"window_s": dt, "per_inserter": data},
    }


# ---- 7 + 8. loaders, power interface, belt throughput ---------------------------------


def _loader_rig_code(o: Tile, rows: list[dict[str, Any]]) -> str:
    return (
        lua.PRELUDE
        + f"local rows = {lua.lit(rows)}\n"
        + """
local E = defines.direction.east
storage.fgen_belt = {}
local out = {}
for i, r in ipairs(rows) do
  local y = r.y
  local res = {belt=r.belt, loader=r.loader}
  local src = S.create_entity{name='infinity-chest', position={r.x, y}, force=F}
  src.infinity_container_filters = {{index=1, name='iron-plate', count=200, mode='exactly'}}
  local l1 = S.create_entity{name=r.loader, position={r.x + 1.5, y}, direction=E,
                             type='output', force=F}
  for k = 0, r.len - 1 do
    S.create_entity{name=r.belt, position={r.x + 3 + k, y}, direction=E, force=F}
  end
  local l2 = S.create_entity{name=r.loader, position={r.x + 3 + r.len + 0.5, y}, direction=E,
                             type='input', force=F}
  local sink = S.create_entity{name='steel-chest', position={r.x + 5 + r.len, y}, force=F}
  res.created = (l1 ~= nil and l2 ~= nil)
  storage.fgen_belt[i] = {sink=sink, count=0}
  out[i] = res
end
-- count and empty the sink every tick, so it never fills up
script.on_nth_tick(1, function()
  for _, r in pairs(storage.fgen_belt) do
    local inv = r.sink.get_inventory(defines.inventory.chest)
    local n = inv.get_item_count('iron-plate')
    if n > 0 then r.count = r.count + n; inv.clear() end
  end
end)
"""
        + lua.emit("out")
    )


def check_loaders_and_power(
    srv: HeadlessServer, gd: GameData, window_s: float = 60.0, belt_len: int = 12
) -> tuple[Result, Result]:
    o = band_origin(7)
    tps = int(gd.constant("ticks_per_second"))
    rows = []
    for k, (belt, loader) in enumerate(LOADERS.items()):
        if belt in gd.entities:
            rows.append(
                {
                    "belt": belt,
                    "loader": loader,
                    "x": o.x + 2.5,
                    "y": o.y + 2.5 + 2 * k,
                    "len": belt_len,
                }
            )
    created = srv.rcon.lua_json(_loader_rig_code(o, rows))
    created = created if isinstance(created, list) else list(created.values())
    eei = srv.rcon.lua_json(
        lua.PRELUDE
        + POWER_FN
        + f"local p = power({o.x + 2}, {o.y + 12})\n"
        + "local ins = S.create_entity{name='inserter', "
        + f"position={{{o.x + 4.5}, {o.y + 12.5}}}, force=F}}\n"
        + lua.emit("{pole_net=p and p.electric_network_id, inserter_net=ins.electric_network_id}")
    )
    srv.wait_ticks(belt_len * tps // 2 + 2 * tps)  # fill the belts
    read = (
        "local out = {tick=game.tick, c={}}\nfor i, r in ipairs(storage.fgen_belt) do "
        "out.c[i] = r.count end\n" + lua.emit("out")
    )
    a = srv.rcon.lua_json(read)
    srv.wait_until_tick(a["tick"] + round(window_s * tps))
    b = srv.rcon.lua_json(read)
    srv.rcon.lua("script.on_nth_tick(nil)")
    dt = (b["tick"] - a["tick"]) / tps
    ca = a["c"] if isinstance(a["c"], list) else list(a["c"].values())
    cb = b["c"] if isinstance(b["c"], list) else list(b["c"].values())
    flows = {}
    for r, x, y in zip(rows, ca, cb, strict=True):
        measured = (y - x) / dt
        flows[r["belt"]] = {
            "loader": r["loader"],
            "measured_per_s": round(measured, 3),
            "predicted_per_s": beltgeom.items_per_second(gd, r["belt"]),
        }
    loaders_ok = all(c.get("created") for c in created)
    match = all(
        abs(f["measured_per_s"] - f["predicted_per_s"]) <= 0.02 * f["predicted_per_s"]
        for f in flows.values()
    )
    powered = eei["inserter_net"] is not None and eei["inserter_net"] == eei["pole_net"]
    r7 = {
        "id": 7,
        "item": "loader / electric-energy-interface availability",
        "assumption": "hidden loaders and electric-energy-interface can be created by script "
        "and loaders keep up with their belt",
        "holds": loaders_ok and powered and match,
        "conclusion": (
            f"loaders {'created' if loaders_ok else 'NOT created'} (direction = belt flow "
            "for both type='output' (chest->belt) and type='input'); "
            f"electric-energy-interface {'powers' if powered else 'does NOT power'} a pole "
            f"network; loader-fed belts {'run at' if match else 'DIFFER from'} full belt speed"
        ),
        "data": {"rows": created, "power": eei, "flows": flows},
    }
    yellow = flows.get("transport-belt")
    ok8 = yellow is not None and abs(yellow["measured_per_s"] - yellow["predicted_per_s"]) <= (
        0.02 * yellow["predicted_per_s"]
    )
    r8 = {
        "id": 8,
        "item": "belt throughput (speed x ticks x 8)",
        "assumption": "items_per_second = speed * 60 * 8 (yellow 15/s)",
        "holds": ok8,
        "conclusion": ", ".join(
            f"{b} {f['measured_per_s']:g}/s measured vs {f['predicted_per_s']:g}/s"
            for b, f in flows.items()
        )
        + f" over {dt:g} s of game time",
        "data": {"window_s": dt, "flows": flows},
    }
    return r7, r8


_loader_cache: dict[int, tuple[Result, Result]] = {}


def check_loaders(srv: HeadlessServer, gd: GameData) -> Result:
    r7, r8 = check_loaders_and_power(srv, gd)
    _loader_cache[id(srv)] = (r7, r8)
    return r7


def check_belt_speed(srv: HeadlessServer, gd: GameData) -> Result:
    cached = _loader_cache.pop(id(srv), None)
    if cached is None:
        _clear(srv, band_area(7))
        try:
            cached = check_loaders_and_power(srv, gd)
        finally:
            _clear(srv, band_area(7))
    return cached[1]


# ---- 9. pole supply area edge ---------------------------------------------------------


def check_supply_area(srv: HeadlessServer, gd: GameData) -> Result:
    """Inserters on every tile of a ring around a pole, just inside and just outside the
    supply square predicted by ``geom.pole.supply_tiles``; compare with the game's
    ``electric_network_id``."""
    o = band_origin(9)
    data = {}
    holds = True
    for k, pname in enumerate(("small-electric-pole", "medium-electric-pole")):
        c = Tile(o.x + 8 + 20 * k, o.y + 9)
        pos = footprint_center(gd, pname, c)
        supplied = pole.supply_tiles(gd, pname, pos)
        xs = [t.x for t in supplied]
        r = (max(xs) - min(xs)) // 2 + 2
        cand = [
            Tile(c.x + dx, c.y + dy)
            for dx in range(-r, r + 1)
            for dy in range(-r, r + 1)
            if Tile(c.x + dx, c.y + dy) not in pole_tiles(gd, pname, pos)
        ]
        specs = [{"name": "inserter", "position": [t.x + 0.5, t.y + 0.5]} for t in cand]
        code = (
            lua.PRELUDE
            + f"S.create_entity{{name={lua.lit(pname)}, position={lua.lit([pos.x, pos.y])}, "
            + "force=F}\n"
            + f"local specs = {lua.lit(specs)}\nlocal out = {{}}\n"
            + "for i, sp in ipairs(specs) do sp.force = F; local e = S.create_entity(sp); "
            + "out[i] = e.electric_network_id ~= nil end\n"
            + lua.emit("out")
        )
        got = srv.rcon.lua_json(code)
        got = got if isinstance(got, list) else [got[str(i + 1)] for i in range(len(cand))]
        wrong = [
            [t.x - c.x, t.y - c.y] for t, g in zip(cand, got, strict=True) if g != (t in supplied)
        ]
        holds = holds and not wrong
        data[pname] = {"predicted_square": max(xs) - min(xs) + 1, "mismatched_offsets": wrong}
    return {
        "id": 9,
        "item": "pole supply area",
        "assumption": "an entity is supplied iff one of its tiles lies fully inside the "
        "supply square (half side supply_area_distance)",
        "holds": holds,
        "conclusion": "1x1 consumers: game coverage equals geom.pole.supply_tiles"
        if holds
        else "game coverage differs from geom.pole.supply_tiles; see data",
        "data": data,
    }


def footprint_center(gd: GameData, name: str, tile: Tile) -> Vec2:
    from fgen.geom import footprint

    return footprint.center_for_tile_anchor(gd, name, tile)


def pole_tiles(gd: GameData, name: str, pos: Vec2) -> frozenset[Tile]:
    from fgen.geom import footprint

    return footprint.tiles_of(gd, name, pos, Direction.N)


CHECKS: dict[int, Callable[[HeadlessServer, GameData], Result]] = {
    1: check_inserter_direction,
    2: check_pole_autoconnect,
    3: check_underground,
    4: check_belt_lanes,
    5: check_blueprint_format,
    6: check_hand_size,
    7: check_loaders,
    8: check_belt_speed,
    9: check_supply_area,
}


def run(
    srv: HeadlessServer, gd: GameData, ids: list[int] | None = None, speed: float = 10.0
) -> list[Result]:
    """Run the checks (all by default) at ``game.speed = speed``; timing is by tick."""
    out = []
    srv.rcon.lua_json(lua.generate_chunks((0, 0), 4))
    srv.set_speed(speed)
    try:
        for i in ids or sorted(CHECKS):
            _clear(srv, band_area(i))
            try:
                out.append(CHECKS[i](srv, gd))
            finally:
                _clear(srv, band_area(i))
    finally:
        srv.set_speed(1)
    return out


__all__ = ["CHECKS", "run"]
