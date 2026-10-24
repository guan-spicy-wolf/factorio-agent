"""M4 (phase 2): measured ore output of mining-patch units on painted ore.

Each output interface's measured rate must be within 5% of its contract
(plan-phase2 §4 M4); every drill must be powered and keep finding ore.
"""

from __future__ import annotations

import pytest

from fgen.gen.mining_patch import generate
from fgen.gen.mining_sweep import CASES, TOLERANCE, case_terrain, compare, contract
from fgen.sim.mining import painted_measurer

pytestmark = pytest.mark.headless

MEASURED = ("rect-1belt", "rect-2belt", "l-shape", "lane-capacity")


@pytest.fixture(scope="module")
def measure(server, gd):
    return painted_measurer(server, gd)


@pytest.mark.parametrize("name", MEASURED)
def test_measured_matches_contract(measure, gd, name):
    case = next(c for c in CASES if c.name == name)
    patch, terrain = case_terrain(case)
    unit = generate(case.params, gd, patch, terrain)
    c = contract(unit)
    m = measure(case, unit, terrain)
    assert m["machines"] == c["drills"]
    assert m["power"]["inserter_networks"] == 1 and m["power"]["unpowered_inserters"] == 0
    assert m["power"]["harness_on_unit_network"]
    assert len(m["sink_throughput"]) == len(c["interfaces"])
    assert "no_minable_resources" not in m["machine_status_probes"]
    assert m["drills_without_target"] == []
    cmp = compare(c, m)
    assert cmp["max_abs_deviation"] <= TOLERANCE, (c["interfaces"], m["sink_samples"])
    if name == "rect-2belt":
        assert len(c["interfaces"]) == 2
    if name == "lane-capacity":
        assert c["dropped_by_reason"].get("lane_capacity")
