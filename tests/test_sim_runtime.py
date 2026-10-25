"""Phase 4b task B: research everything on the test server and read the force state."""

from __future__ import annotations

import pytest

from fgen.sim import runtime

pytestmark = pytest.mark.headless


def test_research_all_sets_bonuses(server):
    before = runtime.read(server.rcon)
    assert before.technologies_total > 0
    state = runtime.prepare(server)
    assert state.source == "force"
    assert state.technologies_researched > before.technologies_researched
    assert state.inserter_stack_size_bonus > 0
    assert state.bulk_inserter_capacity_bonus > state.inserter_stack_size_bonus
    assert state.mining_drill_productivity_bonus > 0
    # idempotent: a second call only reads
    assert runtime.prepare(server) == state
