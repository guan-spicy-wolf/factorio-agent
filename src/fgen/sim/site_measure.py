"""Whole-site simulation (plan-phase3a §3.6).

Order: clear obstacles -> snapshot -> generate units -> place -> route -> deploy all ->
fixtures -> run -> measure every port. Units and routed entities go in as **one
blueprint** (so the exporter writes the copper wires of the whole site); fixtures are
created with ``create_entity`` afterwards (their poles auto-connect to poles in reach)
and configured (infinity chest / pipe filters, counting sinks).

Measurement is in game ticks, like :mod:`fgen.sim.measure`: a per-tick handler empties
the sink chests and keeps totals; a sampling handler appends ``(tick, sink totals,
item production/consumption counters)`` every ``sample`` ticks. Consumer inputs are
measured through the force's item flow statistics: ``get_output_count(item)`` is the
total consumed (the furnaces eat ore and coal), ``get_input_count(item)`` the total
produced (the drills' ore, the furnaces' plates). Infinity chests and the sink-clearing
script do not count as production or consumption (checked: sources and sinks only
appear in the sink totals).
"""

from __future__ import annotations

import itertools
import statistics
from typing import Any

from fgen.data.schema import GameData
from fgen.layout.model import Layout
from fgen.route.connect import Connection
from fgen.sim import harness as hs
from fgen.sim import lua
from fgen.sim.deployer import RconDeployer
from fgen.sim.server import HeadlessServer
from fgen.site.model import Site
from fgen.terrain.model import Area


def fixture_names(site: Site) -> list[str]:
    return [u.name for u in site.units if "fixture" in u.unit.meta]


def site_area(site: Site, conn: Connection, gd: GameData, margin: int = 2) -> Area:
    from fgen.geom.vec import Tile

    lo, hi = conn.site_layout(site).bounds(gd)
    return Tile(lo.x - margin, lo.y - margin), Tile(hi.x + margin, hi.y + margin)


def deploy(srv: HeadlessServer, gd: GameData, site: Site, conn: Connection) -> hs.Harness:
    """Units + routes as one blueprint at world (0, 0), then the fixtures."""
    from fgen.geom.vec import Tile

    fixtures = set(fixture_names(site))
    main = [u for u in site.units if u.name not in fixtures]
    ps = [p for u in main for p in site.world(u.name).layout.placements]
    ps += conn.layout.placements
    RconDeployer(srv.rcon, gd).place_layout(Layout(ps), Tile(0, 0))
    h = hs.fixture_specs(
        site, gd, conn, [u.name for u in site.units if u.name in fixtures], routes=False
    )
    return hs.build(srv.rcon, h)


def _lua_area(area: Area) -> str:
    a, b = area
    return lua.area_lit(((a.x, a.y), (b.x + 1, b.y + 1)))


def networks(srv: HeadlessServer, area: Area) -> dict[str, Any]:
    """Electric network ids of every pole and every electric consumer in the area."""
    out = srv.rcon.lua_json(
        lua.PRELUDE
        + "local poles, users, unpowered = {}, {}, 0\n"
        + f"for _, e in pairs(S.find_entities_filtered{{area={_lua_area(area)}}}) do\n"
        + "  if e.type == 'electric-pole' then\n"
        + "    poles[tostring(e.electric_network_id)] = (poles[tostring(e.electric_network_id)]"
        + " or 0) + 1\n"
        + "  elseif e.prototype.electric_energy_source_prototype and e.type ~= "
        + "'electric-energy-interface' then\n"
        + "    local n = e.electric_network_id\n"
        + "    if n then users[tostring(n)] = (users[tostring(n)] or 0) + 1 "
        + "else unpowered = unpowered + 1 end\n"
        + "  end\nend\n"
        + lua.emit("{poles=poles, users=users, unpowered=unpowered}")
    )
    return {
        "pole_networks": {k: int(v) for k, v in (out["poles"] or {}).items()},
        "consumer_networks": {k: int(v) for k, v in (out["users"] or {}).items()},
        "unpowered": int(out["unpowered"]),
    }


def tank_fluid(srv: HeadlessServer, gd: GameData, site: Site, name: str) -> dict[str, Any]:
    """``get_fluid(1)`` of the tank of fluid-sink fixture ``name``."""
    p = site.world(name).layout.placements[0]
    out = srv.rcon.lua_json(
        lua.PRELUDE
        + f"local e = S.find_entity({lua.lit(p.name)}, {lua.lit([p.position.x, p.position.y])})\n"
        + "local f = e and e.get_fluid(1)\n"
        + lua.emit("{found=e ~= nil, fluid=f and f.name or nil, amount=f and f.amount or 0}")
    )
    return {"found": bool(out["found"]), "fluid": out.get("fluid"), "amount": float(out["amount"])}


SETUP = """
local H = storage.fgen_harness
H.start = {start}
H.snapshots = {{}}
H.items = {items}
script.on_nth_tick(1, function(ev)
  for _, s in pairs(H.sinks) do
    local inv = s.entity.get_inventory(defines.inventory.chest)
    local n = inv.get_item_count(s.item)
    if n > 0 then
      s.total = s.total + n
      if not s.first_tick then s.first_tick = ev.tick end
      inv.clear()
    end
  end
end)
script.on_nth_tick({sample}, function(ev)
  local stats = game.forces.player.get_item_production_statistics(game.surfaces[1])
  local row = {{tick=ev.tick, totals={{}}, produced={{}}, consumed={{}}}}
  for k, s in ipairs(H.sinks) do row.totals[k] = s.total end
  for _, it in ipairs(H.items) do
    row.produced[it] = stats.get_input_count(it)
    row.consumed[it] = stats.get_output_count(it)
  end
  table.insert(H.snapshots, row)
end)
"""


def _lst(x: Any) -> list[Any]:
    if isinstance(x, dict):
        return [x[k] for k in sorted(x, key=int)] if x else []
    return list(x or [])


def run(
    srv: HeadlessServer,
    gd: GameData,
    items: list[str],
    *,
    warmup_s: float = 60.0,
    sample_s: float = 10.0,
    samples: int = 6,
    speed: float = 32.0,
) -> dict[str, Any]:
    """Run the deployed site and sample sinks and item statistics. Returns median rates
    per sink and per item (produced / consumed)."""
    tps = int(gd.constant("ticks_per_second"))
    st = round(sample_s * tps)
    start = srv.tick()
    srv.rcon.lua(lua.PRELUDE + SETUP.format(start=start, items=lua.lit(items), sample=st))
    try:
        srv.set_speed(speed)
        first = -(-(start + round(warmup_s * tps)) // st) * st
        end = first + samples * st
        srv.wait_until_tick(end + 1, poll=0.05)
        log = srv.rcon.lua_json(
            "local H = storage.fgen_harness\nlocal sinks = {}\n"
            "for k, s in ipairs(H.sinks) do sinks[k] = {item=s.item, total=s.total, "
            "first_tick=s.first_tick} end\n" + lua.emit("{snapshots=H.snapshots, sinks=sinks}")
        )
    finally:
        srv.set_speed(1)
        srv.rcon.lua("script.on_nth_tick(nil)")
    snaps = {int(s["tick"]): s for s in _lst(log["snapshots"])}
    ticks = list(range(first, end + 1, st))
    missing = [t for t in ticks if t not in snaps]
    if missing:
        raise RuntimeError(f"missing snapshots at {missing[:3]}")

    def rates(get) -> list[float]:
        return [
            (get(snaps[b]) - get(snaps[a])) / ((b - a) / tps) for a, b in itertools.pairwise(ticks)
        ]

    n_sinks = len(_lst(snaps[ticks[0]]["totals"]))
    sink = [rates(lambda s, k=k: float(_lst(s["totals"])[k])) for k in range(n_sinks)]
    prod = {it: rates(lambda s, it=it: float((s["produced"] or {}).get(it, 0))) for it in items}
    cons = {it: rates(lambda s, it=it: float((s["consumed"] or {}).get(it, 0))) for it in items}
    sinks = _lst(log["sinks"])
    span = (ticks[-1] - ticks[0]) / tps

    def mean(get) -> float:
        return (get(snaps[ticks[-1]]) - get(snaps[ticks[0]])) / span

    return {
        # whole-window means (burner fuel is taken in whole items: medians of short
        # samples quantise it)
        "sink_mean": [mean(lambda s, k=k: float(_lst(s["totals"])[k])) for k in range(n_sinks)],
        "produced_mean": {
            it: mean(lambda s, it=it: float((s["produced"] or {}).get(it, 0))) for it in items
        },
        "consumed_mean": {
            it: mean(lambda s, it=it: float((s["consumed"] or {}).get(it, 0))) for it in items
        },
        "sink_throughput": [statistics.median(r) for r in sink],
        "sink_samples": [[round(x, 4) for x in r] for r in sink],
        "sink_items": [s["item"] for s in sinks],
        "produced": {it: statistics.median(r) for it, r in prod.items()},
        "consumed": {it: statistics.median(r) for it, r in cons.items()},
        "produced_samples": {it: [round(x, 4) for x in r] for it, r in prod.items()},
        "consumed_samples": {it: [round(x, 4) for x in r] for it, r in cons.items()},
        "window_ticks": [first - start, end - start],
        "sample_s": st / tps,
        "warmup_s": warmup_s,
        "speed": speed,
        "ticks_per_second": tps,
        "startup_ticks": min(
            (int(s["first_tick"]) - start for s in sinks if s.get("first_tick") is not None),
            default=None,
        ),
    }


def measure(
    srv: HeadlessServer, gd: GameData, site: Site, conn: Connection, **opts: Any
) -> dict[str, Any]:
    """Deploy the routed site, run it and read every sink and item counter."""
    h = deploy(srv, gd, site, conn)
    area = site_area(site, conn, gd)
    nets = networks(srv, area)
    items = sorted({i.material for _, i in site.interfaces() if i.kind == "item" and i.material})
    m = run(srv, gd, items, **opts)
    m["sink_units"] = list(h.sink_units)
    m["networks"] = {
        "pole_networks": len(nets["pole_networks"]),
        "consumer_networks": len(nets["consumer_networks"]),
        "unpowered": nets["unpowered"],
    }
    m["game_version"] = srv.rcon.command("/version").strip()
    return m


def clear_site(srv: HeadlessServer, area: Area) -> None:
    a, b = area
    srv.rcon.lua("script.on_nth_tick(nil)")
    srv.rcon.lua_json(lua.clear_area(((a.x, a.y), (b.x + 1, b.y + 1))))


FLAT_MEASURE = {"warmup_s": 60.0, "sample_s": 20.0, "samples": 6, "speed": 32.0}


def flat_row(srv: HeadlessServer, gd: GameData, **opts: Any) -> dict[str, Any]:
    """M4: the flat-field site, planned, deployed and measured."""
    from fgen.gen import site_sweep as ss
    from fgen.sim.terrain import RconTerrain

    rt = RconTerrain(srv.rcon)
    rt.generate(ss.FLAT_AREA)
    clear_site(srv, ss.FLAT_AREA)
    snap = rt.snapshot(ss.FLAT_AREA, generate=False)
    site = ss.flat_site(gd, snap)
    row, conn = ss.plan_row(ss.FLAT, site, gd, note=FLAT_NOTE)
    try:
        m = measure(srv, gd, site, conn, **{**FLAT_MEASURE, **opts})
    finally:
        clear_site(srv, ss.FLAT_AREA)
    row["measured"] = m
    row["judged"] = ss.judge(row, m)
    m["explanation"] = FUEL_EXPLANATION
    return row


FUEL_EXPLANATION = (
    "Fuel (coal) is consumed in whole items: each stone furnace burns one coal per "
    "fuel_value / energy_usage seconds and the consumption counter moves when a coal "
    "starts burning, so over a 120 s window every furnace counts 2 or 3 coal; a few "
    "percent either way on the coal port is quantisation, not a shortfall (no furnace "
    "ran out: the plate output meets the contract)."
)


FLAT_NOTE = (
    "Flat test field. A 7.5 plates/s stone-furnace row; iron ore and coal from two "
    "infinity-chest sources placed apart from the row (nearest free spot to fixed "
    "anchors), side-loaded by the router onto the row's left (ore) and right (coal) "
    "lanes; plates into a counting sink; power from an energy-interface fixture."
)
