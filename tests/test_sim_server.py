"""M1: server lifecycle, RCON round trip, flat test field."""

from __future__ import annotations

import os
import time

import pytest

from fgen.sim import lua
from fgen.sim.server import HeadlessServer

pytestmark = pytest.mark.headless

HALF = 100  # the plan's 200x200 field around the origin


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # a zombie still answers kill(0); check its state
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().split()[2] != "Z"
    except FileNotFoundError:
        return False


def test_lifecycle_tick_and_clean_stop(headless_workdir):
    srv = HeadlessServer(workdir=headless_workdir)
    with srv:
        pid = srv.pid
        assert pid is not None and _alive(pid)
        t0 = int(srv.rcon.lua("rcon.print(game.tick)"))
        t1 = srv.wait_ticks(10, timeout=30)
        assert t1 >= t0 + 10
        srv.set_speed(4)
        assert float(srv.rcon.lua("rcon.print(game.speed)")) == 4
    assert not srv.running
    deadline = time.monotonic() + 5
    while _alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _alive(pid)


def test_flat_field_is_buildable_and_empty(headless_workdir):
    with HeadlessServer(workdir=headless_workdir) as srv:
        srv.rcon.lua_json(lua.generate_chunks((0, 0), 4))
        rep = srv.rcon.lua_json(lua.terrain_report(((-HALF, -HALF), (HALF, HALF))))
        assert rep["total"] == (2 * HALF) ** 2
        assert rep["blocked"] == 0
        assert not rep["entities"]


def test_lua_error_is_raised(headless_workdir):
    from fgen.sim.rcon import LuaError

    with HeadlessServer(workdir=headless_workdir) as srv:
        with pytest.raises(LuaError, match="boom"):
            srv.rcon.lua('error("boom")')
        big = srv.rcon.lua('rcon.print(string.rep("ab", 20000))').strip()
        assert big == "ab" * 20000
