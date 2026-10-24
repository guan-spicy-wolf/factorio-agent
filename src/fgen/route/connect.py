"""``connect(site)``: route every link of a site and check the result
(plan-phase3a §3.4).

Order: belts, then pipes, then power (poles take one tile and can go almost anywhere,
so they come last). If a belt or pipe link fails, the other order (pipes first, the
failing link first) is tried once; if that fails too, :class:`RouteError` names the
link and the tile closest to its goal.

The router reads only interface contracts: tile, direction, lane, material, rate and
``rate_profile`` (per-lane rates, fixed or on-demand supply), ``requires`` (the port's
and its port group's), ``entity`` (belt tier, pole type), ``port_group`` (which inputs
share one belt) and ``space`` (approach and tiles kept free); never the units' layouts
for port facts (the layouts only mark which tiles are taken). The result is a
:class:`Connection`: the routed entities as one ``Layout``, per
link details, the contract checks, warnings, and ``validate`` run over the whole site
(units + routes).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fgen.data.schema import GameData
from fgen.gen.base import Infeasible
from fgen.layout.model import Interface, Layout, Placement
from fgen.layout.validate import Violation, validate
from fgen.route import belt as rbelt
from fgen.route.grid import Costs, RouteGrid
from fgen.site.model import Endpoint, Link, Site
from fgen.terrain.model import Area


@dataclass
class LinkRoute:
    link: str
    kind: str
    placements: list[Placement]
    mode: str = ""
    lane: str | None = None
    fluid: str | None = None

    def summary(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for p in self.placements:
            counts[p.name] = counts.get(p.name, 0) + 1
        return {
            "link": self.link,
            "kind": self.kind,
            "mode": self.mode,
            "lane": self.lane,
            "entities": counts,
        }


@dataclass
class Connection:
    routes: list[LinkRoute] = field(default_factory=list)
    extra: list[Placement] = field(default_factory=list)  # stubs
    checks: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    violations: list[Violation] = field(default_factory=list)
    order: list[str] = field(default_factory=list)

    def route_fluids(self) -> dict[int, str]:
        """Index in :attr:`layout` -> fluid, for routed pipes."""
        out: dict[int, str] = {}
        k = len(self.extra)
        for r in self.routes:
            for _ in r.placements:
                if r.fluid is not None:
                    out[k] = r.fluid
                k += 1
        return out

    @property
    def layout(self) -> Layout:
        ps = list(self.extra)
        for r in self.routes:
            ps += r.placements
        return Layout(ps)

    def site_layout(self, site: Site) -> Layout:
        return site.merged([self.layout])

    @property
    def ok(self) -> bool:
        return not self.violations and all(c["ok"] is not False for c in self.checks)


def _kind(site: Site, ln: Link) -> str:
    return site.interface(ln.src).kind


def build_grid(site: Site, gd: GameData, bounds: Area | None, costs: Costs | None) -> RouteGrid:
    fluids = site.fluids()
    layouts = []
    base = 0
    for u in site.units:
        w = site.world(u.name)
        n = len(w.layout.placements)
        local = {k - base: f for k, f in fluids.items() if base <= k < base + n}
        layouts.append((u.name, w.layout, local))
        base += n
    g = RouteGrid.build(gd, site.terrain, layouts, bounds=bounds, costs=costs)
    rbelt.reserve_ports(g, site.interfaces())
    return g


def consumer_key(site: Site, ep: Endpoint) -> tuple[str, str]:
    """A consumer is one belt: the input's port group in its unit (or the port alone)."""
    i = site.interface(ep)
    return ep.unit, i.port_group or f"#{ep.iface}"


def _consumers(
    site: Site, gd: GameData, links: list[Link]
) -> dict[tuple[str, str], rbelt.Consumer]:
    out: dict[tuple[str, str], rbelt.Consumer] = {}
    for ln in links:
        dst = site.interface(ln.dst)
        key = consumer_key(site, ln.dst)
        c = out.get(key)
        if c is None:
            if dst.entity is None:
                raise rbelt.RouteError(ln.label, f"consumer port {dst.tile} names no belt entity")
            c = rbelt.Consumer(dst.tile, dst.direction, rbelt.plain_belt(gd, dst.entity))
            c.ifaces = [
                (ep, i)
                for ep, i in site.interfaces()
                if i.kind == "item" and consumer_key(site, ep) == key
            ]
            for ep, i in c.ifaces:
                if (i.tile, i.direction) != (dst.tile, dst.direction):
                    raise Infeasible(f"port group {key}: {ep} is not on the group's port tile")
            c.group = site.world(ln.dst.unit).group(dst.port_group)
            c.label = f"{key[0]}/{key[1]}"
            out[key] = c
        mode, lane = rbelt.link_mode(dst)
        c.links.append((ln, mode, lane))
    for c in out.values():
        modes = {m for _, m, _ in c.links}
        if len(modes) > 1 or (modes == {"direct"} and len(c.links) > 1):
            raise Infeasible(
                f"consumer port {c.tile}: {len(c.links)} links mix direct and side-load "
                "(a 'both' lane input takes exactly one link)"
            )
    return out


def _belt_checks(
    site: Site, gd: GameData, consumers: dict[tuple[str, str], rbelt.Consumer], conn: Connection
) -> None:
    for c in consumers.values():
        supplies = []
        for ln, mode, lane in c.links:
            supplies.append(rbelt.Supply(ln, site.interface(ln.src), mode, lane))
        checks, problems, warnings = rbelt.consumer_report(gd, c, supplies)
        conn.checks += checks
        conn.warnings += warnings
        if problems:
            need = max(
                (s.producer.fixed_rate() or 0.0) for s in supplies
            )  # rough: at least the largest single producer
            raise Infeasible("; ".join(problems), rbelt.capacity_suggestions(gd, c, need))


def _route_belts(
    site: Site,
    gd: GameData,
    grid: RouteGrid,
    links: list[Link],
    consumers: dict[tuple[str, str], rbelt.Consumer],
    conn: Connection,
) -> None:
    for c in consumers.values():
        if not c.side_loaded:
            continue
        allowed = frozenset(str(ep) for ep, _ in c.ifaces)
        for t in c.stub_tiles():
            if not grid.free(t, allowed):
                raise rbelt.RouteError(
                    c.links[0][0].label, f"no room for the side-load stub at {t}", t
                )
        stub = c.stub(gd)
        grid.add(stub, f"stub@{c.tile.x},{c.tile.y}")
        conn.extra += stub
        for ln, _, lane in c.links:
            assert lane is not None
            grid.reserve([c.approach(lane)], str(ln.dst))
    for ln in links:
        src, dst = site.interface(ln.src), site.interface(ln.dst)
        c = consumers[consumer_key(site, ln.dst)]
        mode, lane = rbelt.link_mode(dst)
        if mode == "side":
            assert lane is not None
            side = rbelt.beltgeom.lane_side(c.direction, lane)
            goals = {(c.side_tile(), side.opposite): True}
        else:
            goals = {(dst.tile, dst.direction): False}
        ps = rbelt.search(
            grid,
            c.belt,
            src.tile.neighbor(src.direction),
            src.direction,
            goals,
            frozenset({str(ln.src), str(ln.dst)}),
            ln.label,
        )
        grid.add(ps, ln.label)
        conn.routes.append(LinkRoute(ln.label, "item", ps, mode, lane))
        conn.order.append(ln.label)


def _validate(site: Site, gd: GameData, conn: Connection) -> list[Violation]:
    ifaces: list[Interface] = [i for _, i in site.interfaces()]
    merged = conn.site_layout(site)
    fluids = dict(site.fluids())
    base = len(site.merged().placements)
    for k, f in conn.route_fluids().items():
        fluids[base + k] = f
    return validate(
        merged,
        gd,
        ifaces,
        belt_terminals=site.belt_terminals(),
        fluids=fluids,
        resources=site.terrain.resources,
    )


def connect(
    site: Site,
    gd: GameData,
    *,
    bounds: Area | None = None,
    costs: Costs | None = None,
    power_pole: str | None = None,
) -> Connection:
    problems = site.check(gd)
    if problems:
        raise Infeasible("site placement: " + "; ".join(problems))
    belt_links = [ln for ln in site.links if _kind(site, ln) == "item"]
    consumers = _consumers(site, gd, belt_links)
    first_error: Infeasible | None = None
    for attempt in range(2):
        conn = Connection()
        _belt_checks(site, gd, consumers, conn)
        grid = build_grid(site, gd, bounds, costs)
        order = list(belt_links)
        if attempt == 1 and isinstance(first_error, rbelt.RouteError):
            failing = [ln for ln in order if ln.label == first_error.link]
            order = failing + [ln for ln in order if ln.label != first_error.link]
        try:
            stages = _stages(attempt)
            for stage in stages:
                if stage == "belt":
                    _route_belts(site, gd, grid, order, consumers, conn)
                elif stage == "pipe":
                    _route_pipes(site, gd, grid, conn)
                else:
                    _route_power(site, gd, grid, conn, power_pole)
        except rbelt.RouteError as exc:
            if first_error is None:
                first_error = exc
                continue
            raise first_error from exc
        conn.violations = _validate(site, gd, conn)
        return conn
    assert first_error is not None
    raise first_error


def _stages(attempt: int) -> list[str]:
    return ["belt", "pipe", "power"] if attempt == 0 else ["pipe", "belt", "power"]


def _route_pipes(site: Site, gd: GameData, grid: RouteGrid, conn: Connection) -> None:
    from fgen.route import pipe

    pipe.route_links(site, gd, grid, conn)


def _route_power(
    site: Site, gd: GameData, grid: RouteGrid, conn: Connection, pole_name: str | None
) -> None:
    from fgen.route import power

    power.route_site(site, gd, grid, conn, pole_name)


__all__ = ["Connection", "Endpoint", "LinkRoute", "connect"]
