"""Generate every member of a variant set side by side and rank the rows.

Each member runs ``family.generate`` exactly as it would alone (the anytime solver's
box is a deterministic budget, so a member's period does not depend on how many
members run beside it; only the wall seconds do), in a forked process when ``procs >
1``. Inside a worker the proof driver's own process pool is turned off
(``cpsat.PROCS = 1``): members are the parallelism, nested pools would oversubscribe
the box. A CP-SAT model takes about 1.5 cores, so ``procs`` of half the cores is the
default the CLI uses.

A row holds hard metrics only: status (``ok`` / ``infeasible`` / ``error``) and the
reason, wall seconds, the predicted throughput and size, the period (size, poles,
inserters, pipes, solver seconds, proven, seconds to the first period, preset metrics,
entities) when the family has one, the blueprint hash, the ASCII render and the
contract; ``memoised`` marks a period the worker had already solved for an earlier
member (same spec), whose wall seconds are therefore not a measurement. Infeasible
members pay for the generator's suggestions (it re-solves for faster belts and smaller
targets), which can dwarf the time box; the first green-chain table spent 4-24 minutes
per full-palette member there (plan-phase9 task B).

:func:`rank` orders the feasible rows by the set's ``rank``: a preset's
lexicographic terms (:data:`fgen.solve.preset.PRESETS`) over the period metrics (area
alone for families without a period), or ``first`` = fewest seconds to the first
period. Ties keep member order.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import asdict
from typing import Any

from fgen.compare.variants import Member, VariantSet, families
from fgen.data.schema import GameData
from fgen.gen.base import Infeasible
from fgen.gen.sweep import _jsonable, blueprint_sha, format_interface
from fgen.layout.render import to_ascii
from fgen.solve.preset import PRESETS

CHARS = {"pipe-to-ground": "u"}
PERIOD_KEYS = (
    "width",
    "height",
    "area",
    "machines",
    "poles",
    "inserters",
    "pipes",
    "seconds",
    "proven",
    "timed_out",
    "unproven_infeasible",
    "preset",
    "metrics",
    "entities",
    "backend",
)

_WORKER: dict[str, Any] = {}


def generate_row(gd: GameData, vset: VariantSet, m: Member) -> dict[str, Any]:
    """One member, in this process."""
    fam = families()[vset.family]
    row: dict[str, Any] = {
        "variant": m.name,
        "family": fam.name,
        "params": _jsonable(asdict(m.params)),
        "overrides": _jsonable(dict(m.overrides)),
        "note": m.note,
        "status": None,
        "wall_s": None,
    }
    t0 = time.monotonic()
    try:
        unit = fam.generate(m.params, gd)
    except Infeasible as exc:
        row.update(
            status="infeasible",
            reason=exc.reason,
            suggestions=list(exc.suggestions),
            wall_s=round(time.monotonic() - t0, 3),
        )
        return row
    except Exception as exc:  # a bug in a generator is a row, not the end of the table
        row.update(
            status="error",
            reason=f"{type(exc).__name__}: {exc}",
            wall_s=round(time.monotonic() - t0, 3),
        )
        return row
    row["wall_s"] = round(time.monotonic() - t0, 3)
    row["status"] = "ok"
    row["predicted"] = _jsonable(fam.predict(unit, gd))
    row["blueprint_sha"] = blueprint_sha(unit, gd)
    row["render"] = to_ascii(unit.layout, gd, chars=CHARS)
    row["interfaces"] = [format_interface(i) for i in unit.interfaces]
    if fam.period:
        per = dict(unit.meta["period"])
        p = {k: per.get(k) for k in PERIOD_KEYS if k in per}
        phases = per.get("phases") or {}
        p["first_feasible_s"] = phases.get("first_feasible_s")
        p["phases"] = dict(phases)
        p["periods"] = unit.meta.get("periods")
        p["ratio"] = unit.meta.get("ratio")
        row["period"] = _jsonable(p)
        # the generator memoises solved specs per process: a member whose spec another
        # member of the same worker already solved returns at once, and its wall seconds
        # say nothing about its cost (the solver statistics are the earlier solve's)
        secs = p.get("seconds") or 0.0
        row["memoised"] = bool(secs) and row["wall_s"] < 0.5 * secs
    return row


def _work(task: tuple[int, Member]) -> tuple[int, dict[str, Any]]:
    from fgen.solve import cpsat

    cpsat.PROCS = 1  # members are the parallelism
    i, m = task
    return i, generate_row(_WORKER["gd"], _WORKER["vset"], m)


def run(
    gd: GameData,
    vset: VariantSet,
    procs: int = 1,
    progress: Callable[[dict[str, Any]], None] | None = None,
    only: set[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """All members of ``vset`` -> ``(rows in member order, meta)``. ``progress`` is
    called with each row as it finishes (parallel: in completion order)."""
    members = [m for m in vset.members() if only is None or m.name in only]
    if only is not None and len(members) < len(only):
        missing = sorted(only - {m.name for m in members})
        raise ValueError(f"{vset.name}: no variant named {missing}")
    meta: dict[str, Any] = {
        "set": vset.name,
        "family": vset.family,
        "rank": vset.rank,
        "procs": max(1, procs),
        "nproc": os.cpu_count(),
        "load_before": _load(),
        "members": len(members),
    }
    t0 = time.monotonic()
    rows: list[dict[str, Any] | None] = [None] * len(members)
    if procs <= 1 or len(members) <= 1:
        for i, m in enumerate(members):
            rows[i] = generate_row(gd, vset, m)
            if progress:
                progress(rows[i])  # type: ignore[arg-type]
    else:
        import multiprocessing as mp

        _WORKER.update(gd=gd, vset=vset)
        with mp.get_context("fork").Pool(min(procs, len(members))) as pool:
            for i, row in pool.imap_unordered(_work, list(enumerate(members)), chunksize=1):
                rows[i] = row
                if progress:
                    progress(row)
    meta["wall_s"] = round(time.monotonic() - t0, 3)
    meta["load_after"] = _load()
    out = [r for r in rows if r is not None]
    for r in out:
        r["key"] = rank_key(r, vset.rank)
    ranked = rank(out, vset.rank)
    for pos, r in enumerate(ranked, 1):
        r["rank"] = pos
    meta["winner"] = ranked[0]["variant"] if ranked else None
    return out, meta


def rank_key(row: dict[str, Any], how: str) -> list[Any] | None:
    """The comparable key of a feasible row under ``how`` (None when not feasible)."""
    if row.get("status") != "ok":
        return None
    per = row.get("period") or {}
    if how == "first":
        ff = per.get("first_feasible_s")
        return [ff if ff is not None else row.get("wall_s")]
    metrics = dict(per.get("metrics") or {})
    if not metrics:
        metrics = {"area": (row.get("predicted") or {}).get("area")}
    terms = PRESETS[how]
    return [metrics.get(t) if metrics.get(t) is not None else float("inf") for t in terms]


def rank(rows: list[dict[str, Any]], how: str) -> list[dict[str, Any]]:
    """Feasible rows, best first; ties keep their order."""
    ok = [r for r in rows if r.get("status") == "ok"]
    return sorted(ok, key=lambda r: rank_key(r, how) or [])


def _load() -> float | None:
    try:
        return round(os.getloadavg()[0], 2)
    except (AttributeError, OSError):
        return None


__all__ = ["generate_row", "rank", "rank_key", "run"]
