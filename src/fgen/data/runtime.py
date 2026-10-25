"""The third data source: force state that research changes (plan-phase4b §2).

Prototypes (seed or dump) and engine constants say what an entity *is*; a
:class:`RuntimeState` says what the force has researched, read from the running game
(``LuaForce``), which changes how fast some of those entities work:

- ``inserter_stack_size_bonus``: added to the hand size of every non-bulk inserter;
- ``bulk_inserter_capacity_bonus``: added to the hand size of bulk inserters;
- ``belt_stack_size_bonus``: items an inserter may stack on one belt slot (recorded;
  no fgen layout uses stacking inserters yet);
- ``mining_drill_productivity_bonus``: extra products per mining cycle (``0.1`` = +10%).

The default instance is a force with nothing researched (all bonuses 0): generators
design for it, so a layout stays valid at any research level; predictions take the
state the measurement ran under (``fgen.gen.throughput``). Nothing here is a game
number: the defaults are "no bonus", every other value comes from the game.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class RuntimeState:
    inserter_stack_size_bonus: float = 0.0
    bulk_inserter_capacity_bonus: float = 0.0
    belt_stack_size_bonus: float = 0.0
    mining_drill_productivity_bonus: float = 0.0
    technologies_researched: int = 0
    technologies_total: int = 0
    current_research: str | None = None
    source: str = "default"  # "default" (nothing researched) or "force" (read in game)

    def hand_bonus(self, bulk: bool) -> float:
        return self.bulk_inserter_capacity_bonus if bulk else self.inserter_stack_size_bonus

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, d: dict[str, Any] | None) -> RuntimeState:
        if not d:
            return cls()
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)

    def describe(self) -> str:
        if self.source == "default":
            return "no research (all bonuses 0)"
        return (
            f"{self.technologies_researched}/{self.technologies_total} technologies; "
            f"inserter hand +{self.inserter_stack_size_bonus:g} (bulk "
            f"+{self.bulk_inserter_capacity_bonus:g}), belt stack +{self.belt_stack_size_bonus:g}, "
            f"mining productivity +{self.mining_drill_productivity_bonus:.0%}"
        )


NO_RESEARCH = RuntimeState()
