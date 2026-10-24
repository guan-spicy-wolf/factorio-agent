"""Whole-site scenarios and their characterization (plan-phase3a §3.6, §4 M4/M5).

Serverless half: build the sites (units + fixtures placed by
:func:`fgen.site.place.find_spot`, links between contracts), route them, list the
contract numbers each port is measured against, and write
``characterization/site.{jsonl,md}`` (rows are kept per case: a run that measures one
case leaves the others as they were). The headless half is
:mod:`fgen.sim.site_measure`.

- ``flat``: a 7.5 plates/s stone-furnace row on the flat field; iron ore and coal come
  from two source fixtures placed away from the row and are side-loaded onto the
  row's ore (left) and fuel (right) lanes by the router; plates go to a sink fixture;
  power from a fixture, chained by the router.
- ``real-map``: nearest iron patch on the normal map -> mining unit (one belt) -> belt
  route -> smelting row placed next to the patch (target = the belt's initial rate,
  at most 7.5/s); coal from a fixture; plates to a sink; one power fixture for both.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from fgen.data.schema import GameData
from fgen.gen.mining_patch import MiningPatchParams
from fgen.gen.mining_patch import generate as gen_mining
from fgen.gen.smelting_row import SmeltingRowParams
from fgen.gen.smelting_row import generate as gen_smelting
from fgen.geom.vec import Direction, Tile
from fgen.layout.model import PlacedUnit, Pose
from fgen.layout.render import LEGEND, TERRAIN_LEGEND, to_ascii
from fgen.route.connect import Connection, connect
from fgen.sim import harness as hs
from fgen.site.model import Endpoint, Link, Site
from fgen.site.place import find_spot
from fgen.terrain.model import Area, Patch, Terrain, background

FLAT = "flat"
REAL_MAP = "real-map"
TOLERANCE = 0.05  # M5: plates >= 95% of min(ore belt, 7.5)
FLAT_AREA: Area = (Tile(-40, -30), Tile(100, 40))
POSES = [Pose(d) for d in (Direction.N, Direction.E, Direction.S, Direction.W)]


def _free(terrain: Terrain, site_units: list[PlacedUnit], gd: GameData):  # -> Callable
    taken: set[Tile] = set()
    for u in site_units:
        taken |= u.world().layout.tiles(gd)
        for i in u.world().interfaces:
            from fgen.site.place import port_tiles

            taken.update(port_tiles(i))

    def free(t: Tile) -> bool:
        return terrain.can_build(t) and t not in taken

    return free


def _iface(unit, kind: str, role: str, material: str | None = None) -> int:
    for k, i in enumerate(unit.interfaces):
        if i.kind == kind and i.role == role and (material is None or i.material == material):
            return k
    raise KeyError((kind, role, material))


def _add_fixture(units, name, fx, gd, terrain, anchor, margin=1):
    pu = find_spot(name, fx, gd, _free(terrain, units, gd), anchor, poses=POSES, margin=margin)
    units.append(pu)
    return pu


def flat_site(gd: GameData, terrain: Terrain, rate: float = 7.5) -> Site:
    p = SmeltingRowParams(target_rate=rate)
    row = gen_smelting(p, gd)
    units = [PlacedUnit("row", row, Tile(0, 0))]
    ore_k = _iface(row, "item", "input", "iron-ore")
    coal_k = _iface(row, "item", "input", "coal")
    out_k = _iface(row, "item", "output")
    pow_k = _iface(row, "power", "input")
    _add_fixture(units, "ore", hs.source_unit(gd, "iron-ore", p.belt), gd, terrain, Tile(-14, -10))
    _add_fixture(units, "coal", hs.source_unit(gd, "coal", p.belt), gd, terrain, Tile(-14, 12))
    w, h = row.layout.size(gd)
    _add_fixture(
        units, "plates", hs.sink_unit(gd, "iron-plate", p.belt), gd, terrain, Tile(w + 8, h + 6)
    )
    _add_fixture(units, "power", hs.power_unit(gd, p.pole), gd, terrain, Tile(-10, h + 8))
    links = [
        Link(Endpoint("ore", 0), Endpoint("row", ore_k), "ore"),
        Link(Endpoint("coal", 0), Endpoint("row", coal_k), "coal"),
        Link(Endpoint("row", out_k), Endpoint("plates", 0), "plates"),
        Link(Endpoint("power", 0), Endpoint("row", pow_k), "power"),
    ]
    return Site(terrain, units, links)


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
    (the lane the belt will be side-loaded onto). A fixed selection rule, not a search
    over layouts: the generator's arrangement is unchanged."""
    import math

    from fgen.geom import mining as mining_geom

    base = base or MiningPatchParams(max_belts=1)
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


def real_map_site(
    gd: GameData,
    terrain: Terrain,
    patch: Patch,
    *,
    mining: MiningPatchParams | None = None,
    max_rate: float = 7.5,
) -> Site:
    """Mining unit on ``patch`` (one belt), a smelting row next to it, fixtures."""
    mp = mining or MiningPatchParams(max_belts=1)
    unit = gen_mining(mp, gd, patch, terrain)
    outs = [k for k, i in enumerate(unit.interfaces) if i.kind == "item" and i.role == "output"]
    (ore_out,) = outs
    ore_iface = unit.interfaces[ore_out]
    rate = min(ore_iface.rate or 0.0, max_rate)
    sp = SmeltingRowParams(target_rate=rate, belt=mp.belt)
    row = gen_smelting(sp, gd)
    units = [PlacedUnit("mine", unit)]
    # the row goes next to the patch, downstream of the ore belt's end
    d = ore_iface.direction
    anchor = ore_iface.tile.neighbor(d, 10)
    units.append(
        find_spot("row", row, gd, _free(terrain, units, gd), anchor, poses=POSES, margin=2)
    )
    pr = units[-1].world()
    ore_k = _iface(row, "item", "input", "iron-ore")
    coal_k = _iface(row, "item", "input", "coal")
    out_k = _iface(row, "item", "output")
    pow_k = _iface(row, "power", "input")
    coal_port = pr.interfaces[coal_k]
    _add_fixture(
        units,
        "coal",
        hs.source_unit(gd, "coal", sp.belt),
        gd,
        terrain,
        coal_port.tile.neighbor(coal_port.direction.opposite, 6),
    )
    out_port = pr.interfaces[out_k]
    _add_fixture(
        units,
        "plates",
        hs.sink_unit(gd, "iron-plate", sp.belt),
        gd,
        terrain,
        out_port.tile.neighbor(out_port.direction, 6),
    )
    pw = pr.interfaces[pow_k]
    _add_fixture(units, "power", hs.power_unit(gd, sp.pole), gd, terrain, pw.tile)
    links = [
        Link(Endpoint("mine", ore_out), Endpoint("row", ore_k), "ore"),
        Link(Endpoint("coal", 0), Endpoint("row", coal_k), "coal"),
        Link(Endpoint("row", out_k), Endpoint("plates", 0), "plates"),
        Link(Endpoint("power", 0), Endpoint("row", pow_k), "power"),
    ]
    return Site(terrain, units, links)


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
                row["initial"] = rp.initial if rp else i.rate
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
    case: str, site: Site, gd: GameData, **extra: Any
) -> tuple[dict[str, Any], Connection]:
    conn = connect(site, gd)
    row = {
        "case": case,
        "site": site.describe(),
        "params": {
            u.name: u.unit.meta.get("params") for u in site.units if u.unit.meta.get("params")
        },
        "ports": ports(site),
        "connection": describe_connection(site, conn, gd),
        "render": render(site, conn, gd),
        "measured": None,
        **extra,
    }
    return row, conn


def judge(row: dict[str, Any], measured: dict[str, Any]) -> dict[str, Any]:
    """Measured value (whole-window mean) and deviation per port (inputs: consumption
    of the material; outputs: the sink's count, else production of the material)."""
    sinks = {name: k for k, name in enumerate(measured.get("sink_units", []))}
    out = []
    for p in row["ports"]:
        if p["role"] == "input":
            got = measured["consumed_mean"].get(p["material"])
            ref = p["min_rate"]
        else:
            k = sinks.get(p.get("sink"))
            got = (
                measured["sink_mean"][k]
                if k is not None
                else measured["produced_mean"].get(p["material"])
            )
            ref = p["initial"]
        out.append(
            {
                "port": p["port"],
                "material": p["material"],
                "contract": ref,
                "measured": got,
                "deviation": None if not ref or got is None else round(got / ref - 1, 4),
            }
        )
    return {"ports": out}


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
        "| port | material | contract/s | measured/s | Δ |",
        "| --- | --- | --- | --- | --- |",
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
            f"| {p['port']} | {p['material']} | {_fmt(p['contract'])} | {_fmt(p['measured'])} "
            f"| {dev} |"
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
        if m.get("explanation"):
            out += ["", m["explanation"]]
    out += ["", "```", r["render"], "```", ""]
    return out
