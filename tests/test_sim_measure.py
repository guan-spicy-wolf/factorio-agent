"""M4: measured throughput of the smelting row in headless Factorio.

Targets from the plan: 5 and 7.5/s on a yellow belt with stone and steel furnaces, and
15/s on a red belt, must be met. Quantisation: a sample can be one item short or long,
so the median is allowed one item per sample period below the target.
"""

from __future__ import annotations

import pytest

from fgen.gen import throughput as tp
from fgen.gen.smelting_row import SmeltingRowParams, generate
from fgen.geom.vec import Tile
from fgen.layout.compare import same
from fgen.sim.measure import RconCharacterizer

pytestmark = pytest.mark.headless

SAMPLE_S = 10.0


@pytest.fixture(scope="module")
def ch(server, gd):
    c = RconCharacterizer(server, gd)
    yield c
    c.clear_all()


@pytest.mark.parametrize(
    "params",
    [
        SmeltingRowParams(target_rate=5),
        SmeltingRowParams(target_rate=7.5),
        SmeltingRowParams(target_rate=5, furnace="steel-furnace"),
        SmeltingRowParams(target_rate=7.5, furnace="steel-furnace", pole="medium-electric-pole"),
        SmeltingRowParams(target_rate=15, belt="fast-transport-belt"),
    ],
    ids=["stone-5", "stone-7.5", "steel-5", "steel-7.5-medium", "stone-15-red"],
)
def test_measured_meets_target(ch, gd, params):
    unit = generate(params, gd)
    m = ch.measure(unit, warmup_s=60, sample_s=SAMPLE_S, samples=6, speed=64)
    assert m["power"]["harness_on_unit_network"] and m["power"]["unpowered_inserters"] == 0
    assert m["power"]["inserter_networks"] == 1
    assert m["machines"] == tp.predict(unit, gd)["machines"]
    assert m["startup_ticks"] is not None
    assert m["throughput"] >= params.target_rate - 1 / SAMPLE_S, m


def test_harness_is_excluded_from_read_back(ch, gd, server):
    unit = generate(SmeltingRowParams(target_rate=5), gd)
    ch.measure(unit, warmup_s=1, sample_s=SAMPLE_S, samples=1, speed=64)
    back = ch.deployer.read_back_layout(unit.layout, ch.origin)
    assert same(unit.layout, back, gd)
    # a wider area picks up the harness too, unless its entities are excluded
    lo, hi = unit.layout.bounds(gd)
    o = ch.origin
    wide = (Tile(o.x + lo.x - 8, o.y + lo.y - 8), Tile(o.x + hi.x + 8, o.y + hi.y + 8))
    assert not same(unit.layout, ch.deployer.read_back(wide, o), gd)
    h = ch.last_harness
    assert same(unit.layout, ch.deployer.read_back(wide, o, exclude=h.unit_numbers), gd)
