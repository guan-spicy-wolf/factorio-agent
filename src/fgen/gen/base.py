"""Generator protocol and the ``Infeasible`` contract (plan §4.5)."""

from __future__ import annotations

from typing import Any, Protocol, TypeVar

from fgen.data.schema import GameData
from fgen.layout.model import Unit
from fgen.layout.validate import Violation

P = TypeVar("P", contravariant=True)


class Infeasible(Exception):
    """The parameters cannot produce a valid unit.

    ``suggestions`` are concrete parameter changes, e.g. ``"sides=2"``; ``changes``
    optionally holds the same changes as dicts (``{"sides": 2}``) for programmatic use.
    """

    def __init__(
        self,
        reason: str,
        suggestions: list[str] | None = None,
        changes: list[dict[str, Any]] | None = None,
    ) -> None:
        self.reason = reason
        self.suggestions = list(suggestions or [])
        self.changes = list(changes or [])
        msg = reason if not self.suggestions else f"{reason} (try: {'; '.join(self.suggestions)})"
        super().__init__(msg)


class Generator(Protocol[P]):
    family: str

    def generate(self, params: P, gd: GameData) -> Unit: ...

    def invariants(self, unit: Unit, gd: GameData) -> list[Violation]: ...
