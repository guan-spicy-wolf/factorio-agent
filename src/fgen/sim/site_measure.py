"""Whole-site simulation (plan-phase3a §3.6; plan-phase7 task A scenarios).

Order: clear obstacles -> snapshot -> generate units -> place (from the links,
:mod:`fgen.site.build`) -> route -> deploy all ->
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
from fgen.gen import site_sweep as ss
from fgen.layout.model import Layout
from fgen.route.connect import Connection
from fgen.sim import harness as hs
from fgen.sim import lua, runtime
from fgen.sim.deployer import RconDeployer
from fgen.sim.server import HeadlessServer
from fgen.site.decide import DEFAULT as DEFAULT_DECISIONS
from fgen.site.decide import Decisions
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
    state = runtime.prepare(srv)  # research everything first (plan-phase4b §2)
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
    m["runtime"] = state.to_json()
    return m


def clear_site(srv: HeadlessServer, area: Area) -> None:
    a, b = area
    srv.rcon.lua("script.on_nth_tick(nil)")
    srv.rcon.lua_json(lua.clear_area(((a.x, a.y), (b.x + 1, b.y + 1))))


FLAT_MEASURE = {"warmup_s": 60.0, "sample_s": 60.0, "samples": 6, "speed": 32.0}


def flat_row(srv: HeadlessServer, gd: GameData, **opts: Any) -> dict[str, Any]:
    """M4: the flat-field site, planned, deployed and measured."""
    from fgen.gen import site_sweep as ss
    from fgen.sim.terrain import RconTerrain

    rt = RconTerrain(srv.rcon)
    rt.generate(ss.FLAT_AREA)
    clear_site(srv, ss.FLAT_AREA)
    snap = rt.snapshot(ss.FLAT_AREA, generate=False)
    row = _measure_plan(
        srv, gd, ss.FLAT, ss.flat_plan(gd, snap), {**FLAT_MEASURE, **opts}, note=FLAT_NOTE
    )
    clear_site(srv, ss.FLAT_AREA)
    row["measured"]["explanation"] = FUEL_EXPLANATION
    return row


FUEL_EXPLANATION = (
    "Fuel (coal) is consumed in whole items: each stone furnace burns one coal per "
    "fuel_value / energy_usage seconds and the consumption counter moves when a coal "
    "starts burning, so over the window every furnace counts a whole number of coal "
    "(phase 4b: the window is 6 x 60 s, about 8 coal per furnace, so the quantisation is "
    "about 1% instead of the 3% of the earlier 120 s window); no furnace ran out: the "
    "plate output meets the contract."
)


FLAT_NOTE = (
    "Flat test field. A 7.5 plates/s stone-furnace row at the site origin; iron ore and "
    "coal from two infinity-chest sources placed from their links (`site.build`: the "
    "cheapest estimated belt to the port they serve), side-loaded by the router onto the "
    "row's left (ore) and right (coal) lanes; plates into a counting sink; power from an "
    "energy-interface fixture."
)


REAL_MEASURE = {"warmup_s": 120.0, "sample_s": 15.0, "samples": 6, "speed": 32.0}
REAL_NOTE = (
    "Normal map (`config/ore-map-gen-settings.json`). Clear obstacles around the nearest "
    "iron patch -> snapshot -> one-belt mining unit (`site_sweep.choose_mining`: highest "
    "belt rate not above the smelting row's ore lane, each variant capped there with "
    "`max_belt_rate`, no drill dry within the window) -> smelting row sized to the belt's "
    "initial rate, placed from its links (`site.build`: cheapest estimated belt from the "
    "ore belt's end, off the ore), fixtures likewise -> router: ore belt side-loaded onto "
    "the row's left lane, coal fixture onto the right lane, plates to a sink, one power "
    "fixture chained to both units."
)
# the red-science chain's gear lane and three belts of buffers fill before the packs
# flow steadily; the science assembler is slow (5 s crafts), so samples are long
RED_MEASURE = {"warmup_s": 240.0, "sample_s": 60.0, "samples": 6, "speed": 32.0}
GREEN_NOTE = (
    "Normal map, one patch per resource (`station_rows`: the answer file's choice, else "
    "the nearest): clear obstacles over the patches and the land between -> snapshot -> "
    "green-science direct chain at the target rate (phase-4b parameters: circuits on a "
    "belt, long-handed inserters, six final machines per period, lane period kind) -> a "
    "supplier per input material by the supplier rule (`site_sweep.supplier_family`): the "
    "circuit chain for circuits, a smelting row per plate sized to the sum of its "
    "consumers' `MinRate` (iron plates feed both chains: one row and a splitter unit, "
    "`gen.junction`), a one-belt mining unit per row -> every free unit placed from its "
    "links -> router; coal fixtures, a pack sink, one power fixture. Nothing here is a "
    "coordinate."
)
RED_NOTE = (
    "Normal map, nearest iron and copper patches (`red_science_rows`): clear obstacles "
    "over both patches and the land between -> snapshot -> red-science direct chain at "
    "the target rate (`direct_chain`, CP-SAT) -> a smelting row per plate input sized to "
    "the chain's `MinRate` -> a one-belt mining unit per row sized to the row's `MinRate` "
    "(`choose_mining`, `max_belt_rate`) -> every free unit placed from its links "
    "(`site.build`: flow order, cheapest estimated belt, off the ore) -> router: ore "
    "side-loaded onto each row's left lane, coal fixtures onto the right lanes, plates "
    "into the chain's input belts as its contract asks (side-load onto a lane or straight "
    "in), packs to a sink, one power fixture for all. Nothing here is a coordinate."
)


def real_map_prepare(
    srv: HeadlessServer,
    gd: GameData,
    resources: list[str],
    *,
    radius: int = 150,
    margin: int = 45,
    min_tiles: int = 50,
    decisions: Decisions | None = None,
    raw: Any | None = None,
) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    """The chosen patch of each resource (``decisions``, default the nearest to the
    spawn), obstacles cleared over all of them and the land between (one rectangle),
    snapshot after clearing. Returns the snapshot, the patches by resource and the run
    info. ``raw`` reuses an earlier radius snapshot (before clearing)."""
    from fgen.geom.vec import Tile
    from fgen.sim import clear
    from fgen.sim.terrain import RconTerrain
    from fgen.terrain.model import nearest_patch

    rt = RconTerrain(srv.rcon)
    whole: Area = (Tile(-radius, -radius), Tile(radius - 1, radius - 1))
    snap = raw if raw is not None else rt.snapshot(whole)
    d = decisions or DEFAULT_DECISIONS
    found = {}
    for res in resources:
        patch = d.choose(snap, res, min_tiles)
        if patch is None:
            raise RuntimeError(f"no {res} patch near the spawn")
        found[res] = patch
    los = [p.bounds[0] for p in found.values()]
    his = [p.bounds[1] for p in found.values()]
    work: Area = (
        Tile(min(t.x for t in los) - margin, min(t.y for t in los) - margin),
        Tile(max(t.x for t in his) + margin, max(t.y for t in his) + margin),
    )
    before = clear.count_entities(srv.rcon, work)
    cleared = clear.clear_obstacles(srv.rcon, work)  # clear -> snapshot, in this order
    snap = rt.snapshot(work, generate=False)
    patches = {}
    for res in resources:
        # the same patch in the cleared snapshot (its id survives clearing)
        patch = (
            nearest_patch(snap, res, found[res].centroid(), min_tiles=1)
            if res in d.patches
            else nearest_patch(snap, res, (0.0, 0.0), min_tiles=min_tiles)
        )
        assert patch is not None
        patches[res] = patch
    info = {
        "decisions": d.name,
        "patches": {res: p.describe() for res, p in patches.items()},
        "work_area": [[work[0].x, work[0].y], [work[1].x, work[1].y]],
        "entities_before_clearing": before,
        "cleared": cleared,
        "cliffs": before.get("cliff", 0),
        "snapshot": {
            "occupied_tiles": len(snap.occupied),
            "water_tiles": len(snap.water),
            "resource_tiles": len(snap.resources),
        },
    }
    return snap, patches, info


def real_map_row(
    srv: HeadlessServer,
    gd: GameData,
    *,
    radius: int = 150,
    margin: int = 45,
    seed: int | None = None,
    **opts: Any,
) -> dict[str, Any]:
    """M5: nearest iron patch -> mining unit -> belt -> smelting row, measured."""
    from fgen.gen import site_sweep as ss
    from fgen.gen.smelting_row import SmeltingRowParams
    from fgen.geom import belt as beltgeom

    mo = {**REAL_MEASURE, **opts}
    snap, patches, info = real_map_prepare(srv, gd, ["iron-ore"], radius=radius, margin=margin)
    patch = patches["iron-ore"]
    info = {"seed": seed, "patch": patch.describe(), **info}
    window = mo["warmup_s"] + mo["samples"] * mo["sample_s"]
    cap = beltgeom.lane_items_per_second(gd, SmeltingRowParams().belt)
    mp = ss.choose_mining(gd, snap, patch, max_rate=cap, window_s=window)
    plan = ss.real_map_plan(gd, snap, patch, mining=mp, max_rate=cap)
    return _measure_plan(srv, gd, ss.REAL_MAP, plan, mo, note=REAL_NOTE, real_map=info)


def red_science_rows(
    srv: HeadlessServer,
    gd: GameData,
    rates: tuple[float, ...] | None = None,
    *,
    radius: int = 150,
    margin: int = 45,
    seed: int | None = None,
    keep_last: bool = False,
    decisions: Decisions | None = None,
    **opts: Any,
) -> list[dict[str, Any]]:
    """Plan-phase7 task A: two patches -> two rows -> red-science chain, one row per
    target rate. With ``keep_last`` the last site stays in the map (for a kept save)."""
    from fgen.gen import site_sweep as ss

    rates = rates or ss.RED_SCIENCE_RATES
    return station_rows(
        srv,
        gd,
        ss.red_science_plan,
        ss.red_case,
        rates,
        RED_NOTE,
        radius=radius,
        margin=margin,
        seed=seed,
        keep_last=keep_last,
        decisions=decisions,
        **opts,
    )


def green_science_rows(
    srv: HeadlessServer,
    gd: GameData,
    rates: tuple[float, ...] | None = None,
    *,
    radius: int = 150,
    margin: int = 45,
    seed: int | None = None,
    keep_last: bool = False,
    decisions: Decisions | None = None,
    **opts: Any,
) -> list[dict[str, Any]]:
    """Plan-phase8 task A: patches -> rows -> circuit chain -> green-science chain (iron
    plates split between the chains), one row per target rate."""
    from fgen.gen import site_sweep as ss

    rates = rates or ss.GREEN_SCIENCE_RATES
    return station_rows(
        srv,
        gd,
        ss.green_science_plan,
        ss.green_case,
        rates,
        GREEN_NOTE,
        radius=radius,
        margin=margin,
        seed=seed,
        keep_last=keep_last,
        decisions=decisions,
        **opts,
    )


def case_name(case: str, decisions: Decisions | None) -> str:
    """A row planned under an answer file is its own case: ``<case>@<name>``."""
    return case if decisions is None or decisions.name == "default" else f"{case}@{decisions.name}"


def station_rows(
    srv: HeadlessServer,
    gd: GameData,
    planner: Any,
    case: Any,
    rates: tuple[float, ...],
    note: str,
    *,
    radius: int,
    margin: int,
    seed: int | None,
    keep_last: bool,
    decisions: Decisions | None,
    **opts: Any,
) -> list[dict[str, Any]]:
    """One measured row per target rate of a science station: choose the patches
    (``decisions``), clear, snapshot, plan with the window the buffers need, measure."""
    mo = {**RED_MEASURE, **opts}
    snap, patches, info = real_map_prepare(
        srv, gd, ["iron-ore", "copper-ore"], radius=radius, margin=margin, decisions=decisions
    )
    info = {"seed": seed, **info}
    base_window = mo["warmup_s"] + mo["samples"] * mo["sample_s"]
    rows = []
    for k, rate in enumerate(rates):
        # the buffers between the stages fill before the flow is steady (fill_time); the
        # mines must not run dry within that longer window either, so plan again with it
        # (the generated chains are reused across the re-plans: ``units``)
        window, units, plan = base_window, {}, None
        for _ in range(3):
            plan = planner(
                gd, snap, patches, rate, window_s=window, units=units, decisions=decisions
            )
            opts_k, _ = warmup_for(srv, gd, plan, mo)
            need = opts_k["warmup_s"] + mo["samples"] * mo["sample_s"]
            if need <= window + 1e-9:
                break
            window = need
        assert plan is not None
        keep = keep_last and k == len(rates) - 1
        rows.append(
            _measure_plan(
                srv,
                gd,
                case_name(case(rate), decisions),
                plan,
                mo,
                keep=keep,
                note=note,
                real_map=info,
                decisions=decisions.to_json() if decisions else None,
            )
        )
    return rows


FILL_FACTOR = ss.FILL_FACTOR
WARMUP_CAP_S = 3600.0  # game seconds; longer fills are measured as they are and flagged


def warmup_for(
    srv: HeadlessServer, gd: GameData, plan: Any, mo: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Measurement options with the warm-up stretched to the site's estimated buffer
    fill time under the force state (research is done first so the state is the one
    measured). Returns ``(options, fill estimate)``."""
    rs = runtime.prepare(srv)
    site, conn, _ = ss.plan_and_connect(plan, gd)
    fill = ss.fill_time(site, conn, gd, rs)
    want = mo["warmup_s"] + FILL_FACTOR * fill["seconds"]
    warm = min(max(mo["warmup_s"], want), WARMUP_CAP_S)
    fill["warmup_s"] = warm
    fill["capped"] = want > WARMUP_CAP_S
    return {**mo, "warmup_s": warm}, fill


def _measure_plan(
    srv: HeadlessServer,
    gd: GameData,
    case: str,
    plan: Any,
    mo: dict[str, Any],
    *,
    keep: bool = False,
    **extra: Any,
) -> dict[str, Any]:
    from fgen.gen import site_sweep as ss

    site, _, log = ss.plan_and_connect(plan, gd)
    row, conn = ss.plan_row(case, site, gd, placement=log, **extra)
    area = site_area(site, conn, gd)
    mo, fill = warmup_for(srv, gd, plan, mo)
    try:
        m = measure(srv, gd, site, conn, **mo)
        m["fill_time"] = fill
    finally:
        if not keep:
            clear_site(srv, area)
    row["measured"] = m
    row["judged"] = ss.judge(row, m, site, gd)
    m["explanation"] = explain(row)
    return row


def explain(row: dict[str, Any]) -> str:
    """One sentence per stage: the final product against its prediction, then the
    anchored supplies (mining ports) against theirs, then any input that lags."""
    judged = row["judged"]["ports"]
    by_port = {p["port"]: p for p in judged}
    sinks = {p["port"] for p in row["ports"] if p.get("sink")}
    finals = [by_port[p] for p in sinks if p in by_port]
    mines = [p for p in judged if p["port"] in _mining_ports(row)]
    parts = []
    for p in finals:
        parts.append(
            f"{p['material']} {p['measured']:.3f}/s = {_pct(p['measured'], p['predicted'])} of "
            f"the predicted {p['predicted']:.4g}/s ({_pct(p['measured'], p['contract'])} of the "
            f"contract {p['contract']:.4g}/s)."
        )
    for p in mines:
        parts.append(
            f"{p['port']} produced {p['measured']:.3f}/s ({p['deviation']:+.1%} vs predicted "
            f"{p['predicted']:.4g}, contract {p['contract']:.4g})."
        )
    lag = [p for p in judged if p["deviation"] is not None and p["deviation"] < -0.01]
    if lag:
        m = row["measured"]
        span = (m["window_ticks"][1] - m["window_ticks"][0]) / m["ticks_per_second"]
        worst = min(lag, key=lambda p: p["deviation"])
        parts.append(
            f"{len(lag)} port(s) lag their prediction by more than 1% (worst {worst['port']} "
            f"{worst['deviation']:+.1%}): items still filling route belts and machine input "
            f"slots over the {span:g} s window (supply equals demand, so buffers fill only "
            "slowly), fuel counted in whole items, or quantisation of a slow flow."
        )
    return " ".join(parts)


def _mining_ports(row: dict[str, Any]) -> set[str]:
    mines = {u["name"] for u in row["site"]["units"] if u["family"] == "anchor/mining-patch"}
    return {p["port"] for p in row["ports"] if p["port"].split("#")[0] in mines}


def _pct(a: float | None, b: float | None) -> str:
    return "?" if not a or not b else f"{a / b:.1%}"
