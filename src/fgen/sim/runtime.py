"""Force state of a running server (plan-phase4b §2): research everything once, then
read the :class:`~fgen.data.runtime.RuntimeState` every measurement records."""

from __future__ import annotations

from typing import Any

from fgen.data.runtime import RuntimeState
from fgen.sim import lua


def _state(out: dict[str, Any]) -> RuntimeState:
    return RuntimeState.from_json({**out, "source": "force"})


def research_all(rcon: Any) -> RuntimeState:
    """``force.research_all_technologies()`` and the resulting state (idempotent)."""
    return _state(rcon.lua_json(lua.research_all()))


def read(rcon: Any) -> RuntimeState:
    return _state(rcon.lua_json(lua.runtime_state()))


def prepare(srv: Any) -> RuntimeState:
    """Research everything on ``srv`` the first time it is asked, then just read the
    state (a measurement records the state it ran under, not the one it assumed)."""
    if not getattr(srv, "_fgen_researched", False):
        research_all(srv.rcon)
        srv._fgen_researched = True
    return read(srv.rcon)
