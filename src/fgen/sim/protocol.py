"""Interfaces to a running Factorio (headless).

Only ``Protocol`` definitions and the ``NotAvailable`` stand-ins (the default when no
server is available) live here; no RCON, socket or subprocess code (plan §4.7). The
headless implementations are ``fgen.sim.deployer.RconDeployer`` and
``fgen.sim.measure.RconCharacterizer``.
"""

from __future__ import annotations

from typing import Any, NoReturn, Protocol

from fgen.geom.vec import Tile
from fgen.layout.model import Layout, Unit

type Area = tuple[Tile, Tile]

MESSAGE = "requires Factorio headless; see docs/VERIFY.md"


class Deployer(Protocol):
    def place(self, unit: Unit, origin: Tile) -> None: ...

    def read_back(self, area: Area) -> Layout: ...


class TerrainQuery(Protocol):
    def buildable(self, tile: Tile) -> bool: ...

    def resources(self, area: Area) -> dict[Tile, tuple[str, int]]: ...

    def water(self, area: Area) -> frozenset[Tile]: ...


class Characterizer(Protocol):
    def measure(self, unit: Unit) -> dict[str, Any]: ...


def _unavailable() -> NoReturn:
    raise RuntimeError(MESSAGE)


class NotAvailableDeployer:
    def place(self, unit: Unit, origin: Tile) -> None:
        _unavailable()

    def read_back(self, area: Area) -> Layout:
        _unavailable()


class NotAvailableTerrain:
    def buildable(self, tile: Tile) -> bool:
        _unavailable()

    def resources(self, area: Area) -> dict[Tile, tuple[str, int]]:
        _unavailable()

    def water(self, area: Area) -> frozenset[Tile]:
        _unavailable()


class NotAvailableCharacterizer:
    def measure(self, unit: Unit) -> dict[str, Any]:
        _unavailable()


NotAvailable = (NotAvailableDeployer, NotAvailableTerrain, NotAvailableCharacterizer)
