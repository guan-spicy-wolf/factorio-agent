"""Junction units (plan-phase8 task A): the splitter that lets one producer feed two
consumers.

The router connects one output port to one consumer belt (a second link from the same
port finds its lead tiles taken), so a material wanted by two units in a site needs a
unit in between. :func:`splitter_unit` is the smallest such unit: an input belt, a
splitter of the belt's tier, and one output belt per splitter half, all facing east in
the unit's own frame (``layout.transform`` turns it like any other unit).

Contract: the input takes the material on the lanes the producer uses (``lane`` and the
per-lane profile are copied from the producer's output port so the outputs can promise
the same lanes: a splitter keeps items on their lane), ``rate`` and ``MinRate`` are the
sum of what the two consumers need; each output is a fixed-rate port carrying its
consumer's planned share. What actually leaves each side is decided by the consumers'
back-pressure (a splitter sends everything to the side that still takes), which the
site-level flow prediction models (``site_sweep.flows``: equal split with overflow).
Capacity is the belt's: one lane's or both lanes' items per second, by the lanes used.
"""

from __future__ import annotations

from typing import Any

from fgen.data.runtime import NO_RESEARCH, RuntimeState
from fgen.data.schema import GameData
from fgen.geom import belt as beltgeom
from fgen.geom import footprint
from fgen.geom.vec import Direction, Tile
from fgen.layout.model import (
    Interface,
    Layout,
    MinRate,
    Placement,
    PortSpace,
    RateProfile,
    Unit,
)

FAMILY = "junction/splitter"
E = Direction.E


def splitter_for_belt(gd: GameData, belt: str) -> str:
    """The splitter of ``belt``'s tier (same speed), from the game data."""
    speed = gd.entity(belt).speed
    for n, e in sorted(gd.entities.items()):
        if e.type == "splitter" and e.speed == speed:
            return n
    raise ValueError(f"no splitter with the speed of {belt}")


def _put(gd: GameData, name: str, anchor: Tile, d: Direction = E) -> Placement:
    return Placement(name, footprint.center_for_tile_anchor(gd, name, anchor, d), d)


def splitter_unit(
    gd: GameData,
    material: str,
    producer: Interface,
    shares: tuple[float, float],
) -> Unit:
    """One belt in, two belts out, for ``material`` coming from ``producer`` (an output
    port: its belt tier and lanes are reused) and wanted at ``shares`` items/s by the two
    consumers (in output order)."""
    belt = producer.entity
    if belt is None:
        raise ValueError("producer port names no belt entity")
    from fgen.route.belt import plain_belt

    belt = plain_belt(gd, belt)
    sp = splitter_for_belt(gd, belt)
    sw, sh = footprint.size(gd, sp, E)  # 1 along the flow, 2 across
    ps = [_put(gd, belt, Tile(0, 0)), _put(gd, sp, Tile(1, 0))]
    ps += [_put(gd, belt, Tile(1 + sw, k)) for k in range(sh)]
    total = float(sum(shares))
    lane = producer.lane
    # lane shares of the input: what the producer promises per lane, scaled to the
    # demand; None when the producer gives no per-lane figure
    plane = {ln: producer.lane_rate(ln) for ln in ("left", "right")}
    given = sum(v for v in plane.values() if v)
    lanes = {ln: v / given for ln, v in plane.items() if v} if given else None

    def profile(rate: float) -> RateProfile:
        return RateProfile.fixed(
            rate, None if lanes is None else {ln: rate * f for ln, f in lanes.items()}
        )

    inp = Interface(
        "item",
        material,
        Tile(0, 0),
        E,
        lane,
        total,
        "input",
        ["splitter: the two outputs share this belt; what each side gets follows its consumer"],
        requires=(MinRate(total),),
        entity=belt,
        space=PortSpace.item("direct"),
    )
    outs = [
        Interface(
            "item",
            material,
            Tile(1 + sw, k),
            E,
            lane,
            float(r),
            "output",
            [f"planned share {r:g}/s of {total:g}/s; overflow goes to the other side"],
            rate_profile=profile(float(r)),
            entity=belt,
            space=PortSpace.item("direct"),
        )
        for k, r in enumerate(shares)
    ]
    meta = {
        "family": FAMILY,
        "params": {"material": material, "belt": belt, "shares": list(map(float, shares))},
        "belt_terminals": [],
    }
    return Unit(Layout(ps), [inp, *outs], meta)


def predict(unit: Unit, gd: GameData, rs: RuntimeState = NO_RESEARCH) -> dict[str, Any]:
    """Capacity of the junction: the input belt's items per second over the lanes the
    producer uses (both when the lane is ``both``)."""
    inp = next(i for i in unit.interfaces if i.role == "input")
    assert inp.entity is not None
    per_lane = beltgeom.lane_items_per_second(gd, inp.entity)
    n = int(gd.constant("belt_lanes")) if inp.lane == "both" else 1
    return {"throughput": per_lane * n, "limits": {"belt": per_lane * n}}


__all__ = ["FAMILY", "predict", "splitter_for_belt", "splitter_unit"]
