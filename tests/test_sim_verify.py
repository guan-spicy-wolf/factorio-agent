"""M3: the in-game VERIFY.md checks. Each must reach a conclusion; where the game
contradicted a phase-0 assumption the code was changed, and the check asserts the fix."""

from __future__ import annotations

import pytest

from fgen.sim import verify

pytestmark = pytest.mark.headless


@pytest.fixture(scope="module")
def results(server, gd):
    return {r["id"]: r for r in verify.run(server, gd, speed=20)}


def test_every_check_concludes(results):
    assert sorted(results) == sorted(verify.CHECKS)
    for r in results.values():
        assert r["conclusion"] and r["holds"] in (True, False)


@pytest.mark.parametrize("cid", [1, 3, 4, 5, 6, 7, 8, 9])
def test_assumption_holds(results, cid):
    r = results[cid]
    assert r["holds"], r


def test_poles_need_explicit_wires(results):
    """Contradicted assumption: poles do not auto-connect; the exporter now writes
    ``wires`` and that yields one network."""
    r = results[2]
    assert r["holds"] is False
    assert r["exporter_writes_wires_ok"] is True
