"""M5 (phase 3a): real map end to end (own module: one server at a time)."""

from __future__ import annotations

import pytest

from fgen.sim import site_measure

pytestmark = pytest.mark.headless


def test_real_map_end_to_end(ore_server, gd):
    row = site_measure.real_map_row(ore_server, gd)
    m = row["measured"]
    assert row["connection"]["violations"] == []
    assert m["networks"] == {"pole_networks": 1, "consumer_networks": 1, "unpowered": 0}
    ports = {p["port"]: p for p in row["judged"]["ports"]}
    ore = next(p for k, p in ports.items() if k.startswith("mine#"))
    plates = next(p for p in ports.values() if p["material"] == "iron-plate")
    target = min(ore["contract"], 7.5)
    assert plates["measured"] >= 0.95 * target, (row["judged"], m["sink_samples"])
