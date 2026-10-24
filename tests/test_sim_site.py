"""M4 (phase 3a): the flat-field site measured against its contracts."""

from __future__ import annotations

import pytest

from fgen.sim import site_measure

pytestmark = pytest.mark.headless


def test_flat_site_meets_contract(server, gd):
    row = site_measure.flat_row(server, gd)
    m = row["measured"]
    assert row["connection"]["violations"] == []
    assert all(c["ok"] is not False for c in row["connection"]["checks"])
    assert m["networks"] == {"pole_networks": 1, "consumer_networks": 1, "unpowered": 0}
    ports = {p["port"]: p for p in row["judged"]["ports"]}
    plates = ports["row#2"]
    # 7.5/s target; one item of quantisation per sample allowed
    assert plates["measured"] >= plates["contract"] - 1 / m["sample_s"], m["sink_samples"]
    assert ports["row#0"]["measured"] >= ports["row#0"]["contract"] - 1 / m["sample_s"]
    assert ports["row#1"]["measured"] > 0  # coal burned: the right lane carries coal
    assert abs(m["produced"]["iron-plate"] - plates["measured"]) <= 1 / m["sample_s"]
