"""Whole-site scenarios and their characterization (plan-phase3a §3.6, §4 M4/M5;
plan-phase7 task A).

Serverless half: describe each site as a :class:`fgen.site.build.SitePlan` (anchored
units, free units, links), let :mod:`fgen.site.build` place the free units from the
links (no hand-written coordinates or anchors: the first unit of an anchorless site sits
at the origin, every other unit goes where its links are cheapest), route it, list the
contract numbers each port is measured against, predict the steady flow under a force
state, and write ``characterization/site.{jsonl,md}`` (rows are kept per case: a run
that measures one case leaves the others as they were). The headless half is
:mod:`fgen.sim.site_measure`.

- ``flat``: a 7.5 plates/s stone-furnace row on the flat field; iron ore and coal come
  from two source fixtures side-loaded onto the row's ore (left) and fuel (right) lanes
  by the router; plates go to a sink fixture; power from a fixture.
- ``real-map``: nearest iron patch on the normal map -> mining unit (one belt) -> belt
  route -> smelting row (target = the belt's initial rate, at most 7.5/s); coal from a
  fixture; plates to a sink; one power fixture for both.
- ``red-science-<rate>``: nearest iron and copper patches -> two one-belt mining units
  sized to what the rows need -> two smelting rows sized to what the science chain's
  input contracts need -> the red-science direct chain; coal fixtures, a pack sink, one
  power fixture. Everything downstream of the patches is sized from the next unit's
  contract (``MinRate``) and placed from the links.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from fgen.data.runtime import RuntimeState
from fgen.data.schema import GameData
from fgen.gen import direct_chain as dc
from fgen.gen import throughput as tp
from fgen.gen.direct_chain import DirectChainParams
from fgen.gen.direct_chain import generate as gen_chain
from fgen.gen.mining_patch import MiningPatchParams
from fgen.gen.mining_patch import generate as gen_mining
from fgen.gen.smelting_row import SmeltingRowParams
from fgen.gen.smelting_row import generate as gen_smelting
from fgen.geom.vec import Tile
from fgen.layout.model import Interface, PlacedUnit, Unit
from fgen.layout.render import LEGEND, TERRAIN_LEGEND, to_ascii
from fgen.route.connect import Connection, connect
from fgen.sim import harness as hs
from fgen.site.build import SitePlan, plan_and_connect
from fgen.site.model import Endpoint, Link, Site
from fgen.terrain.model import Area, Patch, Terrain, background

FLAT = "flat"
REAL_MAP = "real-map"
RED_SCIENCE = "red-science"
RED_SCIENCE_RATES = (0.5, 1.0)  # packs/s tiers of the red-science scenario
TOLERANCE = 0.05  # M5: plates >= 95% of min(ore belt, 7.5)
FLAT_AREA: Area = (Tile(-40, -30), Tile(100, 40))
MINING_FAMILY = "anchor/mining-patch"
FILL_FACTOR = 1.25  # measured warm-up = base + this x the estimated buffer fill time


def red_case(rate: float) -> str:
    return f"{RED_SCIENCE}-{rate:g}"


def _iface(unit: Unit, kind: str, role: str, material: str | None = None) -> int:
    for k, i in enumerate(unit.interfaces):
        if i.kind == kind and i.role == role and (material is None or i.material == material):
            return k
    raise KeyError((kind, role, material))


def _item_ports(unit: Unit, role: str) -> list[tuple[int, Interface]]:
    return [
        (k, i)
        for k, i in enumerate(unit.interfaces)
        if i.kind == "item" and i.role == role and i.material is not None
    ]


def add_fixtures(plan: SitePlan, name: str, unit: Unit, gd: GameData) -> None:
    """A source fixture for every unlinked item input of ``name``, a sink for every
    unlinked item output, and (once per plan) a power fixture linked to its power
    input. Fixture names are ``<unit>.<material>`` / ``<unit>.power``."""
    linked = {ep for ln in plan.links for ep in (ln.src, ln.dst) if ep.unit == name}
    for k, i in _item_ports(unit, "input"):
        if Endpoint(name, k) in linked:
            continue
        fx = f"{name}.{i.material}"
        plan.free.append((fx, hs.source_unit(gd, str(i.material), _belt(i))))
        plan.links.append(Link(Endpoint(fx, 0), Endpoint(name, k), fx))
    for k, i in _item_ports(unit, "output"):
        if Endpoint(name, k) in linked:
            continue
        fx = f"{name}.{i.material}"
        plan.free.append((fx, hs.sink_unit(gd, str(i.material), _belt(i))))
        plan.links.append(Link(Endpoint(name, k), Endpoint(fx, 0), fx))
    has_power = any(u.unit.meta.get("fixture", {}).get("kind") == "power" for u in plan.fixed)
    has_power |= any(u.meta.get("fixture", {}).get("kind") == "power" for _, u in plan.free)
    for k, i in enumerate(unit.interfaces):
        if i.kind == "power" and i.role == "input" and not has_power:
            if i.entity is None:
                raise ValueError(f"{name}#{k}: power port names no pole")
            plan.free.append(("power", hs.power_unit(gd, i.entity)))
            plan.links.append(Link(Endpoint("power", 0), Endpoint(name, k), "power"))
            has_power = True


def _belt(i: Interface) -> str:
    if i.entity is None:
        raise ValueError(f"item port at {i.tile} names no belt entity")
    return i.entity


# ---- scenarios -------------------------------------------------------------------------


def flat_plan(gd: GameData, terrain: Terrain, rate: float = 7.5) -> SitePlan:
    """M4: one smelting row, every port on a fixture."""
    p = SmeltingRowParams(target_rate=rate)
    row = gen_smelting(p, gd)
    plan = SitePlan(terrain, free=[("row", row)])
    add_fixtures(plan, "row", row, gd)
    return plan


def flat_site(gd: GameData, terrain: Terrain, rate: float = 7.5) -> Site:
    return plan_and_connect(flat_plan(gd, terrain, rate), gd)[0]


def choose_mining(
    gd: GameData,
    terrain: Terrain,
    patch: Patch,
    *,
    max_rate: float,
    window_s: float,
    base: MiningPatchParams | None = None,
) -> MiningPatchParams:
    """One-belt mining params for the end-to-end site: for each axis and flow, raise
    ``min_ore_amount_per_drill`` until no drill can run dry within ``window_s`` (as in
    phase 2), then keep the variant with the highest belt rate not above ``max_rate``
    (what the consumer takes). Every variant is asked for at most ``max_rate`` per belt
    (``max_belt_rate``: the generator cuts upstream drills). A fixed selection rule, not
    a search over layouts; the patch's resource comes from ``patch``."""
    from fgen.geom import mining as mining_geom

    base = replace(
        base or MiningPatchParams(max_belts=1), resource=patch.resource, max_belt_rate=max_rate
    )
    need = math.ceil(mining_geom.drill_rate(gd, base.drill, base.resource) * window_s)
    best: tuple[float, MiningPatchParams] | None = None
    for axis in ("x", "y"):
        for flow in ("+", "-"):
            p = replace(base, axis=axis, flow=flow)  # type: ignore[arg-type]
            u = gen_mining(p, gd, patch, terrain)
            while (u.meta["anchor"]["first_dry_s_lower_bound"] or 0) < window_s:
                p = replace(p, min_ore_amount_per_drill=max(need, p.min_ore_amount_per_drill * 2))
                u = gen_mining(p, gd, patch, terrain)
            rate = u.meta["anchor"]["rate"]
            if rate <= max_rate + 1e-9 and (best is None or rate > best[0]):
                best = (rate, p)
    if best is None:
        raise ValueError(f"every one-belt variant exceeds {max_rate:g}/s")
    return best[1]


def _one_output(unit: Unit) -> tuple[int, Interface]:
    outs = _item_ports(unit, "output")
    if len(outs) != 1:
        raise ValueError(f"expected one item output, got {len(outs)}")
    return outs[0]


def real_map_plan(
    gd: GameData,
    terrain: Terrain,
    patch: Patch,
    *,
    mining: MiningPatchParams | None = None,
    max_rate: float = 7.5,
) -> SitePlan:
    """M5: mining unit on ``patch`` (one belt) -> a smelting row sized to the belt's
    initial rate (at most ``max_rate``) -> fixtures."""
    mp = mining or MiningPatchParams(max_belts=1, resource=patch.resource)
    unit = gen_mining(mp, gd, patch, terrain)
    ore_out, ore_iface = _one_output(unit)
    rate = min(ore_iface.fixed_rate() or 0.0, max_rate)
    sp = SmeltingRowParams(target_rate=rate, belt=mp.belt)
    row = gen_smelting(sp, gd)
    ore_k = _iface(row, "item", "input", ore_iface.material)
    plan = SitePlan(
        terrain,
        fixed=[PlacedUnit("mine", unit)],
        free=[("row", row)],
        links=[Link(Endpoint("mine", ore_out), Endpoint("row", ore_k), "ore")],
    )
    add_fixtures(plan, "row", row, gd)
    return plan


def real_map_site(
    gd: GameData,
    terrain: Terrain,
    patch: Patch,
    *,
    mining: MiningPatchParams | None = None,
    max_rate: float = 7.5,
) -> Site:
    plan = real_map_plan(gd, terrain, patch, mining=mining, max_rate=max_rate)
    return plan_and_connect(plan, gd)[0]


def smelting_for(need: Interface, gd: GameData, **params: Any) -> SmeltingRowParams:
    """The smelting row that feeds input port ``need`` (its material, its ``MinRate``)."""
    product = str(need.material)
    recipe = next(
        (n for n, r in sorted(gd.recipes.items()) if tp.main_product(gd, n)[0] == product),
        None,
    )
    if recipe is None:
        raise ValueError(f"no recipe makes {product}")
    rate = need.min_rate() or need.fixed_rate() or 0.0
    return SmeltingRowParams(recipe=recipe, target_rate=rate, belt=_belt(need), **params)


def red_science_plan(
    gd: GameData,
    terrain: Terrain,
    patches: dict[str, Patch],
    rate: float,
    *,
    window_s: float,
    chain: DirectChainParams | None = None,
    mining: MiningPatchParams | None = None,
    chain_unit: Unit | None = None,
) -> SitePlan:
    """Two patches (keyed by the plate they end up as: ``iron-plate`` -> iron patch,
    ``copper-plate`` -> copper patch) -> two one-belt mining units -> two smelting rows ->
    the red-science chain at ``rate`` packs/s. Each stage is sized from the next stage's
    input contract: the rows from the chain's ``MinRate``, the mines from the rows'.
    ``chain_unit`` reuses an already generated chain (the solver takes a while)."""
    cp = replace(chain or DirectChainParams(recipe="automation-science-pack"), target_rate=rate)
    unit = chain_unit if chain_unit is not None else gen_chain(cp, gd)
    plan = SitePlan(terrain, free=[("science", unit)])
    for k, need in _item_ports(unit, "input"):
        patch = patches.get(str(need.material))
        if patch is None:
            continue  # no patch for it: a fixture supplies it
        stage = str(need.material).removesuffix("-plate")
        sp = smelting_for(need, gd)
        row = gen_smelting(sp, gd)
        row_name = f"{stage}-row"
        ore_k, ore_port = next(
            (j, i) for j, i in _item_ports(row, "input") if i.material == patch.resource
        )
        mp = choose_mining(
            gd,
            terrain,
            patch,
            max_rate=ore_port.min_rate() or ore_port.fixed_rate() or 0.0,
            window_s=window_s,
            base=mining,
        )
        mine = gen_mining(mp, gd, patch, terrain)
        mine_name = f"{stage}-mine"
        out_k, _ = _one_output(mine)
        plan.fixed.append(PlacedUnit(mine_name, mine))
        plan.free.append((row_name, row))
        mine_out, row_out = Endpoint(mine_name, out_k), Endpoint(row_name, _one_output(row)[0])
        plan.links.append(Link(mine_out, Endpoint(row_name, ore_k), f"{stage}-ore"))
        plan.links.append(Link(row_out, Endpoint("science", k), f"{stage}-plate"))
        add_fixtures(plan, row_name, row, gd)
    add_fixtures(plan, "science", unit, gd)
    return plan


# ---- contracts per port ---------------------------------------------------------------


def ports(site: Site) -> list[dict[str, Any]]:
    """The unit (non-fixture) ports a site run is judged on: every linked item input
    (contract = ``MinRate``, also the interface's ``rate``) and item output (contract =
    ``rate_profile.initial``)."""
    out = []
    for ln in site.links:
        for ep in (ln.src, ln.dst):
            if "fixture" in site.placed(ep.unit).unit.meta:
                continue
            i = site.interface(ep)
            if i.kind != "item" or i.material is None:
                continue
            row = {
                "port": str(ep),
                "link": ln.label,
                "role": i.role,
                "material": i.material,
                "lane": i.lane,
            }
            if i.role == "input":
                row["min_rate"] = i.min_rate()
                row["rate"] = i.rate
            else:
                rp = i.rate_profile
                row["initial"] = i.fixed_rate()
                row["depletion_s_lower_bound"] = rp.depletion_s_lower_bound if rp else None
                peer = ln.dst if ep == ln.src else ln.src
                row["sink"] = peer.unit if "fixture" in site.placed(peer.unit).unit.meta else None
            out.append(row)
    return out


def describe_connection(site: Site, conn: Connection, gd: GameData) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for p in conn.layout.placements:
        counts[p.name] = counts.get(p.name, 0) + 1
    return {
        "routes": [r.summary() for r in conn.routes],
        "entities": counts,
        "checks": conn.checks,
        "warnings": conn.warnings,
        "violations": [f"{v.code}: {v.message}" for v in conn.violations],
    }


def render(site: Site, conn: Connection, gd: GameData, margin: int = 2) -> str:
    lay = conn.site_layout(site)
    lo, hi = lay.bounds(gd)
    area = (lo.translate(-margin, -margin), hi.translate(margin, margin))
    return to_ascii(lay, gd, rulers=True, background=background(site.terrain, None, area))


def plan_row(
    case: str,
    site: Site | SitePlan,
    gd: GameData,
    *,
    placement: list[dict[str, Any]] | None = None,
    **extra: Any,
) -> tuple[dict[str, Any], Connection]:
    """Place (for a plan) and route a site; the characterization row without
    measurement. ``row["placement"]`` records the placement attempts of a plan (or
    ``placement`` for a site built beforehand)."""
    if isinstance(site, SitePlan):
        site, conn, placement = plan_and_connect(site, gd)
    else:
        conn = connect(site, gd)
    row = {
        "case": case,
        "site": site.describe(),
        "params": {
            u.name: u.unit.meta.get("params") for u in site.units if u.unit.meta.get("params")
        },
        "ports": ports(site),
        "connection": describe_connection(site, conn, gd),
        "placement": placement,
        "render": render(site, conn, gd),
        "measured": None,
        **extra,
    }
    return row, conn


# ---- prediction --------------------------------------------------------------------------
#
# Per generator family, the unit's own steady output under a force state (its
# ``predict``'s ``throughput``); a family missing here cannot be predicted. Anchored
# supply (mining) is read off the port contract instead: per-lane drill rates times the
# mining productivity, each lane capped by the belt.

CAPACITY: dict[str, Callable[[Unit, GameData, RuntimeState], dict[str, Any]]] = {
    "belt-fed-row/smelting": tp.predict,
    dc.DirectChain.family: dc.predict,
}


def register_capacity(family: str, fn: Callable[[Unit, GameData, RuntimeState], dict[str, Any]]):
    CAPACITY[family] = fn


def port_supply(i: Interface, unit: Unit, gd: GameData, rs: RuntimeState) -> float:
    """What a fixed-supply output port delivers under ``rs``: a mining port's drills x
    rate x (1 + mining productivity) per lane, each lane capped by the belt."""
    if unit.meta.get("family") == MINING_FAMILY and i.rate_profile and i.rate_profile.lanes:
        cap = tp.belt_lane_capacity(gd, i.entity or unit.meta["params"]["belt"])
        bonus = 1.0 + rs.mining_drill_productivity_bonus
        return sum(min(v * bonus, cap) for _, v in i.rate_profile.lanes)
    return i.fixed_rate() or 0.0


def flows(site: Site, gd: GameData, rs: RuntimeState) -> dict[str, dict[str, Any]]:
    """Per producing unit (not fixtures, not anchors) under the force state ``rs``:
    ``cap`` (what it can make: its own throughput and what its inputs receive) and
    ``actual`` (what it makes once its consumers' belts have backed up), by two passes
    over the units in link order; ``supply`` per endpoint is what each item link's source
    can deliver (:func:`port_supply` for anchors, ``cap`` of a unit, None for a fixture).

    Forward (capacity): a unit makes at most its own throughput under ``rs``
    (:data:`CAPACITY`) and at most what each input receives divided by that input's
    share of the output (the contract's input ``rate`` over output ``rate``: furnace
    capacity to furnace capacity, period flows to period flows); a fixture delivers on
    demand, an anchored unit :func:`port_supply`. Backward (demand): a unit only makes
    what its consumers take (belts back up), a sink takes everything."""
    units = {u.name: u.unit for u in site.units}
    fixture = {n for n, u in units.items() if "fixture" in u.meta}
    into: dict[str, list[Link]] = {n: [] for n in units}
    outof: dict[str, list[Link]] = {n: [] for n in units}
    for ln in site.links:
        if site.interface(ln.src).kind != "item":
            continue
        into[ln.dst.unit].append(ln)
        outof[ln.src.unit].append(ln)
    seq: list[str] = []  # topological order over item links
    pending = {n: len(into[n]) for n in units}
    ready = sorted(n for n, k in pending.items() if k == 0)
    while ready:
        n = ready.pop(0)
        seq.append(n)
        for ln in outof[n]:
            pending[ln.dst.unit] -= 1
            if pending[ln.dst.unit] == 0:
                ready.append(ln.dst.unit)
                ready.sort()
    if len(seq) != len(units):
        raise ValueError("item links form a cycle")
    out: dict[str, dict[str, Any]] = {}
    for n in seq:
        u = units[n]
        if n in fixture or u.meta.get("family") == MINING_FAMILY or not _item_ports(u, "output"):
            continue
        fam = u.meta.get("family")
        if fam not in CAPACITY:
            raise ValueError(f"no capacity predictor for family {fam!r} ({n})")
        c = CAPACITY[fam](u, gd, rs)["throughput"]
        supply: dict[str, float | None] = {}
        for ln in into[n]:
            src = ln.src.unit
            if src in fixture:
                supply[str(ln.dst)] = None
                continue
            if src in out:
                got = out[src]["cap"]
            else:
                got = port_supply(site.interface(ln.src), units[src], gd, rs)
            supply[str(ln.dst)] = got
            sh = _share(units, n, ln.dst.iface)
            if sh > 0:
                c = min(c, got / sh)
        out[n] = {"cap": c, "actual": c, "supply": supply, "into": into[n], "outof": outof[n]}
    for n in reversed(seq):
        if n not in out:
            continue
        a = out[n]["cap"]
        for ln in out[n]["outof"]:
            if ln.dst.unit in fixture:
                continue  # a sink takes everything
            a = min(a, out[ln.dst.unit]["actual"] * _share(units, ln.dst.unit, ln.dst.iface))
        out[n]["actual"] = a
    return out


def _share(units: dict[str, Unit], name: str, k: int) -> float:
    """Input port ``k``'s rate per unit of the unit's output, from the contract."""
    u = units[name]
    _, out = _one_output(u)
    i = u.interfaces[k]
    return (i.fixed_rate() or 0.0) / (out.fixed_rate() or 1.0)


def predict(site: Site, gd: GameData, rs: RuntimeState) -> dict[str, float]:
    """Steady flow per judged port under the force state ``rs`` (:func:`flows`): every
    output port carries its unit's ``actual``; every input, and the port that feeds it,
    follows the unit's actual output by the contract's input/output rate share."""
    units = {u.name: u.unit for u in site.units}
    fixture = {n for n, u in units.items() if "fixture" in u.meta}
    out: dict[str, float] = {}
    for n, f in flows(site, gd, rs).items():
        for ln in f["outof"]:
            out[str(ln.src)] = f["actual"]
        for ln in f["into"]:
            drawn = f["actual"] * _share(units, n, ln.dst.iface)
            out[str(ln.dst)] = drawn
            if ln.src.unit not in fixture:
                out[str(ln.src)] = drawn
    return out


def fill_time(site: Site, conn: Connection, gd: GameData, rs: RuntimeState) -> dict[str, Any]:
    """How long (game seconds) until every belt between producers and consumers has
    backed up, i.e. until the producers run at what their consumers draw: per item link
    whose source can deliver more than is drawn, the belt buffer (route tiles of the
    link plus the consumer's own belt tiles, at ``belt_items_per_tile`` per tile, one
    lane when the producer uses one) over the surplus; the largest wins. Machine input
    slots are not counted (a few items). The contract has no buffer or start-up field
    (plan-phase3a §6 item 8); this is the site-level stand-in for it."""
    units = {u.name: u.unit for u in site.units}
    per_tile = float(gd.constant("belt_items_per_tile"))
    lanes_total = float(gd.constant("belt_lanes"))
    routes = {r.link: r for r in conn.routes}
    fl = flows(site, gd, rs)
    worst: dict[str, Any] = {"seconds": 0.0, "link": None}
    details = []
    for n, f in fl.items():
        for ln in f["into"]:
            got = f["supply"].get(str(ln.dst))
            if got is None:
                continue  # fixture: on demand, nothing backs up
            drawn = f["actual"] * _share(units, n, ln.dst.iface)
            surplus = got - drawn
            if surplus <= 1e-9:
                continue
            src = site.interface(ln.src)
            lanes = 1.0 if src.lane in ("left", "right") else lanes_total
            r = routes.get(ln.label)
            route_tiles = sum(
                1
                for p in (r.placements if r else [])
                if gd.entity(p.name).type in ("transport-belt", "underground-belt")
            )
            own = sum(
                1
                for p in units[n].layout.placements
                if gd.entity(p.name).type in ("transport-belt", "underground-belt")
            )
            items = (route_tiles + own) * per_tile * lanes / lanes_total
            secs = items / surplus
            details.append(
                {
                    "link": ln.label,
                    "supply": round(got, 4),
                    "drawn": round(drawn, 4),
                    "buffer_items": round(items),
                    "seconds": round(secs),
                }
            )
            if secs > worst["seconds"]:
                worst = {"seconds": secs, "link": ln.label}
    return {**worst, "links": details}


def judge(
    row: dict[str, Any],
    measured: dict[str, Any],
    site: Site | None = None,
    gd: GameData | None = None,
) -> dict[str, Any]:
    """Measured value (whole-window mean) and deviation per port (inputs: consumption
    of the material; outputs: the sink's count, else production of the material), from
    the prediction under the measured force state (``measured.runtime``) when the site is
    given, else from the contract.

    The force statistics are per material, not per port: when several judged ports of
    different units consume (or produce without a sink) the same material, they are
    judged together as one row (``port`` lists them joined by ``+``, contract and
    prediction summed); phase 7 changed this from one row per port, each showing the
    force-wide number (see ``docs/experiments/7-red-science-site.md``)."""
    sinks = {name: k for k, name in enumerate(measured.get("sink_units", []))}
    rs = RuntimeState.from_json(measured.get("runtime"))
    pred = predict(site, gd, rs) if site is not None and gd is not None else {}
    # group the ports that share one force-wide counter
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for p in row["ports"]:
        if p["role"] == "input":
            key = ("consumed", p["material"])
        elif p.get("sink") is not None:
            key = ("sink", p["sink"])
        else:
            key = ("produced", p["material"])
        groups.setdefault(key, []).append(p)
    out = []
    for (how, what), ps in groups.items():
        units = {p["port"].split("#")[0] for p in ps}
        # several ports of one unit (e.g. both plate ports): keep each; of several units
        # (two rows burning coal): one row for the shared counter
        members = [[p] for p in ps] if len(units) == 1 else [ps]
        for group in members:
            refs = [p["min_rate"] if p["role"] == "input" else p["initial"] for p in group]
            ref = None if any(r is None for r in refs) else sum(refs)
            exps = [pred.get(p["port"], p.get("min_rate", p.get("initial"))) for p in group]
            exp = None if any(e is None for e in exps) else sum(exps)
            if how == "consumed":
                got = measured["consumed_mean"].get(what)
            elif how == "sink":
                got = measured["sink_mean"][sinks[what]] if what in sinks else None
            else:
                got = measured["produced_mean"].get(what)
            out.append(
                {
                    "port": "+".join(p["port"] for p in group),
                    "material": group[0]["material"],
                    "contract": ref,
                    "predicted": exp,
                    "measured": got,
                    "deviation": None if not exp or got is None else round(got / exp - 1, 4),
                    "deviation_vs_contract": None
                    if not ref or got is None
                    else round(got / ref - 1, 4),
                }
            )
    return {"ports": out, "runtime": rs.to_json()}


# ---- characterization file ------------------------------------------------------------


def _load(path: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                out[r["case"]] = r
    return out


def write(out_dir: Path, new_rows: list[dict[str, Any]], gd: GameData) -> tuple[Path, Path]:
    """Merge ``new_rows`` into ``site.jsonl`` (by case; an unmeasured new row keeps the
    old measurement when its render is unchanged) and rewrite ``site.md``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    jsonl = out_dir / "site.jsonl"
    rows = _load(jsonl)
    for r in new_rows:
        old = rows.get(r["case"])
        if r.get("measured") is None and old and old.get("render") == r["render"]:
            r = {**r, "measured": old.get("measured"), "judged": old.get("judged")}
        rows[r["case"]] = r
    ordered = [rows[k] for k in sorted(rows, key=lambda c: (c != FLAT, c))]
    jsonl.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in ordered))
    md = [
        "# Sites: units + routes + fixtures",
        "",
        "Generated by `uv run fgen sweep site [--measure] [--real-map]`; do not edit by hand.",
        f"Game data: {gd.source} {gd.version}. The router (`fgen.route.connect`) only reads "
        "the interface contracts; fixtures (`fgen.sim.harness`) are units like any other.",
        "Inputs are measured as the force's consumption of the material (furnaces), outputs "
        "as the sink count; whole-window means in game time after warm-up.",
        "",
        LEGEND,
        TERRAIN_LEGEND,
        "",
    ]
    for r in ordered:
        md += _section(r)
    mdp = out_dir / "site.md"
    mdp.write_text("\n".join(md) + "\n")
    return jsonl, mdp


def _round(d: dict[str, float] | None) -> dict[str, float] | None:
    return None if d is None else {k: round(v, 4) for k, v in d.items() if v}


def _fmt(x: Any) -> str:
    return "-" if x is None else f"{x:.4g}" if isinstance(x, float) else str(x)


def _section(r: dict[str, Any]) -> list[str]:
    c = r["connection"]
    out = [f"## {r['case']}", ""]
    if r.get("note"):
        out += [r["note"], ""]
    out += [
        "Units: "
        + ", ".join(
            f"`{u['name']}` ({u['family']}) at ({u['origin'][0]},{u['origin'][1]}) "
            f"{u['rotation']}{' mirrored' if u['mirror'] else ''}"
            for u in r["site"]["units"]
        ),
        "",
        "Routes: "
        + "; ".join(
            f"{x['link']} [{x['kind']}{' ' + x['mode'] if x['mode'] else ''}"
            f"{' ' + x['lane'] if x['lane'] else ''}] "
            + ", ".join(f"{n} {k}" for n, k in sorted(x["entities"].items()))
            for x in c["routes"]
        ),
        "",
        "Contract checks: "
        + "; ".join(
            f"{x['interface']} {x['predicate']} -> {x['ok']} ({x['detail']})" for x in c["checks"]
        ),
        "",
        f"Warnings: {'; '.join(c['warnings']) or '-'}. Violations (whole site): "
        f"{'; '.join(c['violations']) or 'none'}.",
        "",
        "| port | material | contract/s (no research) | predicted/s (measured force state) | "
        "measured/s | Δ vs predicted |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    judged = (r.get("judged") or {}).get("ports") or [
        {
            "port": p["port"],
            "material": p["material"],
            "contract": p.get("min_rate", p.get("initial")),
            "measured": None,
            "deviation": None,
        }
        for p in r["ports"]
    ]
    for p in judged:
        dev = "-" if p["deviation"] is None else f"{p['deviation']:+.1%}"
        out.append(
            f"| {p['port']} | {p['material']} | {_fmt(p['contract'])} | "
            f"{_fmt(p.get('predicted'))} | {_fmt(p['measured'])} | {dev} |"
        )
    m = r.get("measured")
    if m:
        out += [
            "",
            f"Measured in {m.get('game_version', '?')}: warm-up {m.get('warmup_s')} s, "
            f"{len((m.get('sink_samples') or [[]])[0])} samples of {m.get('sample_s')} s; "
            f"electric networks {m.get('networks')}; consumed (mean) "
            f"{_round(m.get('consumed_mean'))}; produced (mean) {_round(m.get('produced_mean'))}.",
        ]
        if m.get("fill_time") and m["fill_time"].get("seconds"):
            ft = m["fill_time"]
            out += [
                "",
                f"Warm-up {m.get('warmup_s'):.0f} s = base + {FILL_FACTOR} x the estimated "
                f"buffer fill time {ft['seconds']:.0f} s (`site_sweep.fill_time`, slowest "
                f"link `{ft['link']}`: "
                + "; ".join(
                    f"{d['link']} {d['buffer_items']} items / ({d['supply']:g} - {d['drawn']:g})/s "
                    f"= {d['seconds']} s"
                    for d in ft["links"]
                )
                + ").",
            ]
        if m.get("explanation"):
            out += ["", m["explanation"]]
        rt = (r.get("judged") or {}).get("runtime") or m.get("runtime")
        if rt:
            out += [
                "",
                f"Force state: {RuntimeState.from_json(rt).describe()} (after "
                "`force.research_all_technologies()`). `predicted` = `site_sweep.predict`: "
                "each unit makes at most its own throughput under this state (`predict` of its "
                "family) and at most what its inputs receive (a mining port: drills x rate x "
                "(1 + mining productivity), each lane capped), and only what its consumers "
                "take; inputs and the ports feeding them follow the unit's output by the "
                "contract's input/output rate share. Contract values are the no-research "
                "design numbers.",
            ]
    out += ["", "```", r["render"], "```", ""]
    return out
