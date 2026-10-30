"""Variant files: which generator, which base parameters, which members to compare.

A variant file is JSON::

    {
      "name": "green-chain",                 // table name: compare/<name>.{jsonl,md}
      "family": "direct-chain",              // a key of FAMILIES
      "base": {"recipe": "logistic-science-pack", "target_rate": 0.5, "time_limit": 60},
      "variants": [                          // explicit members: overrides on base
        {"name": "pinned-4b", "params": {"palette": "given", ...}, "note": "why"}
      ],
      "grid": {"palette": ["full", "given"], "intermediates": ["lane", "auto"]},
      "rank": "compact",                     // compact | cheap | simple | first
      "asked_by": {"who": "...", "date": "...", "question": "what this set decides"}
    }

``variants`` and ``grid`` may both be given; the grid's members are named by their
values (``palette=full;intermediates=lane``). Every member is ``family.params(**base,
**overrides)``: unknown fields are an error (a typo would otherwise be a silent default),
lists become tuples where the dataclass field holds a tuple. Nothing here is a
coordinate, and nothing here solves anything.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field, fields, is_dataclass
from itertools import product
from pathlib import Path
from typing import Any

from fgen.data.schema import GameData
from fgen.layout.model import Unit

RANKS = ("compact", "cheap", "simple", "first")


@dataclass(frozen=True)
class Family:
    """A generator the runner knows how to drive: its parameter dataclass, ``generate``
    and ``predict``; ``period`` says whether ``unit.meta['period']`` carries solver
    statistics (the direct chain)."""

    name: str
    params: type
    generate: Callable[[Any, GameData], Unit]
    predict: Callable[[Unit, GameData], dict[str, Any]]
    period: bool = False


def _families() -> dict[str, Family]:
    from fgen.gen import direct_chain as dc
    from fgen.gen import smelting_row as sr
    from fgen.gen import throughput as tp

    return {
        "direct-chain": Family(
            "direct-chain", dc.DirectChainParams, dc.generate, dc.predict, period=True
        ),
        "smelting": Family("smelting", sr.SmeltingRowParams, sr.generate, tp.predict),
        # mining needs a terrain snapshot and a patch next to its params: task B
    }


def families() -> dict[str, Family]:
    return _families()


@dataclass(frozen=True)
class Variant:
    name: str
    params: dict[str, Any]  # overrides on the set's base
    note: str = ""


@dataclass
class VariantSet:
    name: str
    family: str
    base: dict[str, Any] = field(default_factory=dict)
    variants: list[Variant] = field(default_factory=list)
    grid: dict[str, list[Any]] = field(default_factory=dict)
    rank: str = "compact"
    asked_by: dict[str, Any] = field(default_factory=dict)
    source: str = ""  # the file it came from (for the table header)

    @classmethod
    def load(cls, path: str | Path) -> VariantSet:
        p = Path(path)
        d = json.loads(p.read_text())
        fam = d.get("family")
        if fam not in families():
            raise ValueError(f"{p}: family must be one of {sorted(families())}, got {fam!r}")
        rank = d.get("rank", "compact")
        if rank not in RANKS:
            raise ValueError(f"{p}: rank must be one of {RANKS}, got {rank!r}")
        variants = []
        for v in d.get("variants") or []:
            if not isinstance(v, dict) or "name" not in v:
                raise ValueError(f"{p}: every variant needs a name, got {v!r}")
            variants.append(Variant(str(v["name"]), dict(v.get("params") or {}), v.get("note", "")))
        grid = {k: list(vs) for k, vs in (d.get("grid") or {}).items()}
        for k, vs in grid.items():
            if not vs:
                raise ValueError(f"{p}: grid axis {k!r} is empty")
        vs = cls(
            name=str(d.get("name") or p.stem),
            family=str(fam),
            base=dict(d.get("base") or {}),
            variants=variants,
            grid=grid,
            rank=rank,
            asked_by=dict(d.get("asked_by") or {}),
            source=str(p),
        )
        vs.members()  # validate field names and uniqueness now
        return vs

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "family": self.family,
            "base": dict(self.base),
            "variants": [
                {"name": v.name, "params": dict(v.params), **({"note": v.note} if v.note else {})}
                for v in self.variants
            ],
            "grid": {k: list(v) for k, v in self.grid.items()},
            "rank": self.rank,
            "asked_by": dict(self.asked_by),
        }

    # -- expansion ----------------------------------------------------------------------
    def members(self) -> list[Member]:
        """Every member with its typed parameters: the explicit variants, then the grid."""
        fam = families()[self.family]
        out: list[Member] = []
        for v in self.variants:
            out.append(Member(v.name, _typed(fam, {**self.base, **v.params}), v.params, v.note))
        if self.grid:
            keys = sorted(self.grid)
            for combo in product(*(self.grid[k] for k in keys)):
                over = dict(zip(keys, combo, strict=True))
                name = ";".join(f"{k}={_short(v)}" for k, v in over.items())
                out.append(Member(name, _typed(fam, {**self.base, **over}), over, ""))
        names = [m.name for m in out]
        dup = sorted({n for n in names if names.count(n) > 1})
        if dup:
            raise ValueError(f"{self.name}: duplicate variant names {dup}")
        if not out:
            raise ValueError(f"{self.name}: no variants (give `variants` and/or `grid`)")
        return out


@dataclass(frozen=True)
class Member:
    name: str
    params: Any  # the family's parameter dataclass
    overrides: dict[str, Any]
    note: str = ""


def _typed(fam: Family, values: dict[str, Any]) -> Any:
    cls = fam.params
    assert is_dataclass(cls)
    known = {f.name: f for f in fields(cls)}
    bad = sorted(set(values) - set(known))
    if bad:
        raise ValueError(f"{fam.name}: unknown parameter(s) {bad}; known: {sorted(known)}")
    typed: dict[str, Any] = {}
    for k, v in values.items():
        f = known[k]
        default = f.default
        if isinstance(v, list) and (isinstance(default, tuple) or "tuple" in str(f.type)):
            v = tuple(v)
        typed[k] = v
    return cls(**typed)


def _short(v: Any) -> str:
    if isinstance(v, list | tuple):
        return "+".join(str(x) for x in v) or "none"
    return str(v)


__all__ = ["RANKS", "Family", "Member", "Variant", "VariantSet", "families"]
