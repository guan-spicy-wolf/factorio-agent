"""``RconCharacterizer``: deploy a unit with its harness and measure it (plan §3.5).

All timing is in game ticks. An in-game ``script.on_nth_tick(1)`` handler empties the
harness sink chests every tick and keeps a running total (and the tick of the first
item); a second handler fires every ``sample`` period and appends ``(tick, total)``
snapshots, and every ``probe`` ticks records each machine's input inventory and
``status``. Python only waits for ``game.tick`` to pass the end and reads the log, so
RCON latency never enters the numbers.
"""

from __future__ import annotations

import itertools
import statistics
from typing import Any

from fgen.data.schema import GameData
from fgen.geom.vec import Tile
from fgen.layout.model import Unit
from fgen.sim import harness as hs
from fgen.sim import lua
from fgen.sim.deployer import RconDeployer, world_area
from fgen.sim.server import HeadlessServer

PROBE_TICKS = 30  # machine-state sampling period
DEFAULT_ORIGIN = Tile(-60, -20)  # inside the verified flat field


def _status_names() -> str:
    return "local names = {}\nfor k, v in pairs(defines.entity_status) do names[v] = k end\n"


def _setup_logging(machine_type: str, area: lua.Area, sample_ticks: int, start: int) -> str:
    return (
        lua.PRELUDE
        + _status_names()
        + f"local machines = S.find_entities_filtered{{area={lua.area_lit(area)}, "
        + f"type={lua.lit(machine_type)}}}\n"
        + "table.sort(machines, function(a, b) if a.position.x ~= b.position.x then "
        + "return a.position.x < b.position.x end return a.position.y < b.position.y end)\n"
        + f"""
local H = storage.fgen_harness
H.start = {int(start)}
H.snapshots = {{}}
H.machines = {{}}
for k, m in ipairs(machines) do
  H.machines[k] = {{entity=m, position=m.position, probes=0, source_empty=0,
                  no_ingredients=0, no_fuel=0, status={{}}}}
end
script.on_nth_tick(1, function(ev)
  for _, s in pairs(H.sinks) do
    local inv = s.entity.get_inventory(defines.inventory.chest)
    local n = inv.get_item_count(s.item)
    if n > 0 then
      s.total = s.total + n
      if not s.first_tick then s.first_tick = ev.tick end
      inv.clear()
    end
  end
end)
script.on_nth_tick({sample_ticks}, function(ev)
  local row = {{tick=ev.tick, totals={{}}}}
  for k, s in ipairs(H.sinks) do row.totals[k] = s.total end
  table.insert(H.snapshots, row)
end)
script.on_nth_tick({PROBE_TICKS}, function(ev)
  if ev.tick < H.measure_from then return end
  for _, m in ipairs(H.machines) do
    local e = m.entity
    if e.valid then
      m.probes = m.probes + 1
      local src = e.get_inventory(defines.inventory.furnace_source)
      if src and src.is_empty() then m.source_empty = m.source_empty + 1 end
      if e.status == defines.entity_status.no_ingredients
         or e.status == defines.entity_status.item_ingredient_shortage then
        m.no_ingredients = m.no_ingredients + 1
      end
      if e.status == defines.entity_status.no_fuel then m.no_fuel = m.no_fuel + 1 end
      local st = names[e.status] or tostring(e.status)
      m.status[st] = (m.status[st] or 0) + 1
    end
  end
end)
H.measure_from = math.huge
"""
        + lua.emit("{machines=#machines}")
    )


READ_LOG = (
    "local H = storage.fgen_harness\n"
    "local ms = {}\n"
    "for k, m in ipairs(H.machines) do ms[k] = {position=m.position, probes=m.probes, "
    "source_empty=m.source_empty, no_ingredients=m.no_ingredients, no_fuel=m.no_fuel, "
    "status=m.status} end\n"
    "local sinks = {}\n"
    "for k, s in ipairs(H.sinks) do sinks[k] = {item=s.item, total=s.total, "
    "first_tick=s.first_tick} end\n"
    + lua.emit("{tick=game.tick, start=H.start, snapshots=H.snapshots, machines=ms, sinks=sinks}")
)


def _lst(x: Any) -> list[Any]:
    if isinstance(x, dict):
        return [x[k] for k in sorted(x, key=int)] if x else []
    return list(x or [])


class RconCharacterizer:
    """Implements ``fgen.sim.protocol.Characterizer``."""

    def __init__(self, srv: HeadlessServer, gd: GameData, origin: Tile | None = None) -> None:
        self.srv, self.gd = srv, gd
        self.origin = DEFAULT_ORIGIN if origin is None else origin
        self.deployer = RconDeployer(srv.rcon, gd)
        self.last_harness: hs.Harness | None = None

    def clear(self, unit: Unit, margin: int = 12) -> None:
        self.srv.rcon.lua("script.on_nth_tick(nil)")
        area = world_area(unit.layout, self.gd, self.origin, margin)
        self.srv.rcon.lua_json(lua.clear_area(area))

    def clear_all(self) -> None:
        """Remove handlers and everything around the measuring origin."""
        self.srv.rcon.lua("script.on_nth_tick(nil)")
        o = self.origin
        self.srv.rcon.lua_json(lua.clear_area(((o.x - 40, o.y - 40), (o.x + 160, o.y + 60))))

    def measure(
        self,
        unit: Unit,
        *,
        warmup_s: float = 60.0,
        sample_s: float = 10.0,
        samples: int = 6,
        speed: float = 32.0,
    ) -> dict[str, Any]:
        gd, srv = self.gd, self.srv
        tps = int(gd.constant("ticks_per_second"))
        sample_ticks = round(sample_s * tps)
        if sample_ticks in (1, PROBE_TICKS) or sample_ticks < 1:
            raise ValueError(f"sample period of {sample_ticks} ticks clashes with the probes")
        self.clear(unit)
        srv.rcon.lua_json(lua.generate_chunks((self.origin.x, self.origin.y), 4))
        srv.set_speed(1)
        try:
            h = hs.plan(unit, gd, self.origin)
            self.deployer.place(unit, self.origin)
            hs.build(srv.rcon, h)
            self.last_harness = h
            machine = gd.entity(unit.meta["machine"]).type
            area = world_area(unit.layout, gd, self.origin)
            start = srv.tick()
            n = srv.rcon.lua_json(_setup_logging(machine, area, sample_ticks, start))["machines"]
            nets = self._power_check(area)
            srv.set_speed(speed)
            measure_from = start + round(warmup_s * tps)
            srv.rcon.lua(f"storage.fgen_harness.measure_from = {measure_from}")
            # the sampling handler fires on multiples of sample_ticks: the window starts
            # at the first snapshot at/after warm-up and spans `samples` periods
            first = -(-measure_from // sample_ticks) * sample_ticks
            end = first + samples * sample_ticks
            srv.wait_until_tick(end + 1, poll=0.05)
            log = srv.rcon.lua_json(READ_LOG)
        finally:
            srv.set_speed(1)
            srv.rcon.lua("script.on_nth_tick(nil)")
        out = self._summarise(unit, log, first, end, sample_ticks, n, nets, start)
        out.update(
            warmup_s=warmup_s,
            speed=speed,
            ticks_per_second=tps,
            game_version=srv.rcon.command("/version").strip(),
        )
        return out

    def _power_check(self, area: lua.Area) -> dict[str, Any]:
        """Electric networks of the unit's inserters (by area; ``unit_number`` lookup
        does not cover inserters) and whether the harness pole is on one of them."""
        out = self.srv.rcon.lua_json(
            lua.PRELUDE
            + "local nets, unpowered = {}, 0\n"
            + f"for _, e in pairs(S.find_entities_filtered{{area={lua.area_lit(area)}, "
            + "type='inserter'}) do\n"
            + "  local n = e.electric_network_id\n"
            + "  if n then nets[tostring(n)] = true else unpowered = unpowered + 1 end\n"
            + "end\n"
            + "local H = storage.fgen_harness\n"
            + "local hn = H.pole and H.pole.electric_network_id or nil\n"
            + lua.emit("{nets=nets, unpowered=unpowered, harness_net=hn}")
        )
        nets = sorted(out["nets"] or {})
        return {
            "inserter_networks": len(nets),
            "unpowered_inserters": out["unpowered"],
            "harness_on_unit_network": out["harness_net"] is not None
            and str(out["harness_net"]) in nets,
        }

    def _summarise(
        self,
        unit: Unit,
        log: dict[str, Any],
        first: int,
        end: int,
        sample_ticks: int,
        n_machines: int,
        power: dict[str, Any],
        start: int,
    ) -> dict[str, Any]:
        tps = self.gd.constant("ticks_per_second")
        snaps = {
            int(s["tick"]): [float(v) for v in _lst(s["totals"])] for s in _lst(log["snapshots"])
        }
        ticks = [t for t in range(first, end + 1, sample_ticks)]
        missing = [t for t in ticks if t not in snaps]
        if missing:
            raise RuntimeError(f"missing sample snapshots at ticks {missing[:3]}")
        rates = []
        for a, b in itertools.pairwise(ticks):
            moved = sum(snaps[b]) - sum(snaps[a])
            rates.append(moved / ((b - a) / tps))
        sinks = _lst(log["sinks"])
        first_ticks = [s.get("first_tick") for s in sinks if s.get("first_tick") is not None]
        input_starved, fuel_starved, source_empty = [], [], []
        status: dict[str, int] = {}
        for m in _lst(log["machines"]):
            probes = int(m["probes"]) or 1
            # share of probes in which the machine could not start for lack of input
            # (status no_ingredients) or fuel (no_fuel)
            input_starved.append(round(int(m["no_ingredients"]) / probes, 3))
            fuel_starved.append(round(int(m["no_fuel"]) / probes, 3))
            # share with an empty source slot; a working furnace often has one (it
            # pulled its last ore into the craft), so this is only a hint
            source_empty.append(round(int(m["source_empty"]) / probes, 3))
            for k, v in (m["status"] or {}).items():
                status[k] = status.get(k, 0) + int(v)
        starved = [
            k
            for k, (a, b) in enumerate(zip(input_starved, fuel_starved, strict=True), start=1)
            if a > 0 or b > 0
        ]
        return {
            "throughput": statistics.median(rates) if rates else 0.0,
            "samples": [round(r, 4) for r in rates],
            "startup_ticks": (min(first_ticks) - start) if first_ticks else None,
            "window_ticks": [first - start, end - start],  # relative to deployment
            "sample_s": sample_ticks / tps,
            "machines": n_machines,
            "machine_input_starved": input_starved,
            "machine_fuel_starved": fuel_starved,
            "machine_source_empty": source_empty,
            "machine_status_probes": status,
            "starved_machines": starved,
            "power": power,
        }
