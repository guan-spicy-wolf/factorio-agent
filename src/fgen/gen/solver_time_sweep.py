"""Characterization of the period solver's time (plan-phase7 §3).

``fgen sweep solver-time [--only CASE,...] [--procs 1,4]`` writes
``characterization/solver_time.{jsonl,md}``: the same specs the other sweeps prove or
solve anytime (the 3:2 circuit chain with given tiers and with the palette, red and
green science, a 12-machine green period), each solved with the proof driver's models
one after another (``procs`` 1: the phase-4b driver) and side by side in forked
processes (``procs`` 4, the default driver since phase 7). The period's placements are
hashed so the table shows the driver changed only the clock. Rows not selected with
``--only`` keep their earlier values (the long rows take 20-40 minutes); every row
records the machine's core count and load, since the box is shared.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from fgen.data.schema import GameData
from fgen.gen import direct_chain as dc
from fgen.gen.direct_chain import DirectChainParams
from fgen.gen.direct_chain_sweep import GREEN, LONG, PHASE_4A, RED, _cells
from fgen.gen.palette_sweep import BASES, phase_4a_vocabulary
from fgen.solve import cpsat

PROCS = (1, 4)


@dataclass(frozen=True)
class Case:
    name: str
    params: DirectChainParams
    note: str
    final_machines: int | None = None  # pick the plan with this many final machines


def cases(gd: GameData) -> list[Case]:
    cp = replace(PHASE_4A, backend="cpsat", time_limit=600.0)
    green = replace(
        cp,
        recipe=GREEN,
        belt_inputs=("electronic-circuit",),
        extra_inserters=(LONG,),
        max_lane_consumers=6,
        target_rate=0.5,
        time_limit=2400.0,
    )
    return [
        Case("circuit-r2-prove", replace(cp, target_rate=2.0), "4a 3:2, given tiers"),
        Case(
            "circuit-fastlong-r2-prove",
            replace(cp, target_rate=2.0, extra_inserters=(LONG,)),
            "4a fast + long-handed (108)",
        ),
        Case("red-lane-r0.5-prove", replace(cp, recipe=RED, target_rate=0.5), "4b red 1:5"),
        Case("red-lane-r1-prove", replace(cp, recipe=RED, target_rate=1.0), "4b red 1:10"),
        Case(
            "red-direct-1:3-prove",
            replace(cp, recipe=RED, target_rate=0.5, intermediates="direct", max_consumers=3),
            "4b red 1:3 (infeasible)",
            final_machines=3,
        ),
        Case(
            "circuit-palette-4a-prove",
            replace(
                BASES["circuit-r2"], prove=True, time_limit=3600.0, exclude=phase_4a_vocabulary(gd)
            ),
            "4c palette without the post-4a vocabulary (108)",
        ),
        Case(
            "circuit-palette-prove",
            replace(BASES["circuit-r2"], prove=True, time_limit=3600.0),
            "4c full palette (99)",
        ),
        Case("green-r0.5-prove", green, "4b green 6:1:1:1"),
        Case("green-r0.5-anytime", replace(green, prove=False, time_limit=60.0), "green, 60 s box"),
        Case(
            "green-12-anytime",
            replace(green, prove=False, max_lane_consumers=12, target_rate=1.0, time_limit=600.0),
            "12 green machines (16 machines), 600 s box",
        ),
    ]


def _spec(c: Case, gd: GameData) -> Any:
    p = dc.resolve_backend(c.params)
    pls = dc.plans(p, gd)
    if c.final_machines is not None:
        pls = [q for q in pls if q.spec.stages[0].count == c.final_machines]
    return pls[0].spec


def placement_sha(per: Any) -> str:
    lay = per.layout()
    items = sorted((q.name, q.position.x, q.position.y, str(q.direction)) for q in lay.placements)
    return hashlib.sha256(repr(items).encode()).hexdigest()[:16]


def _load() -> list[float]:
    return [round(x, 2) for x in os.getloadavg()]


def run_case(c: Case, gd: GameData, procs: int) -> dict[str, Any]:
    spec = _spec(c, gd)
    p = dc.resolve_backend(c.params)
    before = _load()
    old = cpsat.PROCS
    cpsat.PROCS = procs
    t = time.monotonic()
    try:
        res = cpsat.solve(spec, gd, time_limit=p.time_limit, prove=p.prove, preset=p.preset)
    finally:
        cpsat.PROCS = old
    st = res.stats
    per = res.best
    row: dict[str, Any] = {
        "case": c.name,
        "procs": procs,
        "note": c.note,
        "params": asdict(c.params),
        "spec": [(s.recipe, s.count) for s in spec.stages],
        "lane_items": list(spec.lane_items),
        "driver": "prove" if p.prove else "anytime",
        "time_limit": p.time_limit,
        "seconds": round(time.monotonic() - t, 1),
        "first_feasible_s": st.phases.get("first_feasible_s"),
        "proven": st.proven,
        "timed_out": st.timed_out,
        "unproven_infeasible": st.unproven_infeasible,
        "models": st.nodes.get("cp_models", 0),
        "det_s": round(st.nodes.get("cp_det_ms", 0) / 1000, 1),
        "build_s": round(st.nodes.get("cp_build_ms", 0) / 1000, 1),
        "period": None,
        "nproc": os.cpu_count(),
        "load_before": before,
        "load_after": _load(),
        "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if per is not None:
        row["period"] = {
            "width": per.width,
            "height": per.height,
            "area": per.area,
            "poles": per.meta["poles"],
            "inserters": per.meta["inserters"],
            "placements_sha": placement_sha(per),
        }
    return row


def load_previous(path: Path) -> dict[tuple[str, int], dict[str, Any]]:
    out: dict[tuple[str, int], dict[str, Any]] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                out[(r["case"], r["procs"])] = r
    return out


def write(
    gd: GameData,
    out_dir: Path,
    only: tuple[str, ...] = (),
    procs: tuple[int, ...] = PROCS,
    log: Any = None,
) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    jsonl = out_dir / "solver_time.jsonl"
    rows = load_previous(jsonl)
    for c in cases(gd):
        if only and c.name not in only:
            continue
        for n in procs:
            if log:
                print(f"solver-time {c.name} procs={n} ...", file=log, flush=True)
            dc._SOLVED.clear()
            rows[(c.name, n)] = run_case(c, gd, n)
            jsonl.write_text(
                "".join(json.dumps(r, sort_keys=True) + "\n" for _, r in sorted(rows.items()))
            )
    order = {c.name: i for i, c in enumerate(cases(gd))}
    rs = sorted(rows.values(), key=lambda r: (order.get(r["case"], 99), r["procs"]))
    jsonl.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rs))
    md = [
        "# Period solver: time characterization",
        "",
        "Generated by `uv run --extra solver fgen sweep solver-time`; do not edit by hand. "
        f"Game data: {gd.source} {gd.version}. `procs` 1: the proof driver's models one "
        "after another (the phase-4b driver); 4: independent models (the skeleton screens "
        "of a height, the fixed widths in chunks) in forked processes (phase 7). The "
        "models are the same deterministic CP-SAT solves either way, so `placements` (a "
        "hash of the period's entities) must agree between the two rows of a case. "
        "`load` is the 1-minute load average before/after (shared 4-core box; `nproc`). "
        "Experiment log: `docs/experiments/7-solver-speed.md`.",
        "",
        _cells(
            "case",
            "note",
            "driver",
            "procs",
            "limit s",
            "wall s",
            "first feasible s",
            "result",
            "period WxH / poles / ins",
            "placements",
            "models",
            "det s",
            "nproc / load before-after",
            "utc",
        ),
        _cells(*["---"] * 14),
    ]
    for r in rs:
        per = r["period"]
        res = (
            "proven"
            if r["proven"]
            else "unproven infeasible"
            if r["unproven_infeasible"]
            else "timed out"
            if r["timed_out"]
            else "no period"
        )
        if per is None and r["proven"]:
            res = "infeasible (proven)"
        md.append(
            _cells(
                r["case"],
                r["note"],
                r["driver"],
                r["procs"],
                f"{r['time_limit']:g}",
                r["seconds"],
                "-" if r["first_feasible_s"] is None else f"{r['first_feasible_s']:.1f}",
                res,
                "-"
                if per is None
                else f"{per['width']}x{per['height']} / {per['poles']} / {per['inserters']}",
                "-" if per is None else per["placements_sha"],
                r["models"],
                r["det_s"],
                f"{r['nproc']} / {r['load_before'][0]:g}-{r['load_after'][0]:g}",
                r["utc"],
            )
        )
    mdp = out_dir / "solver_time.md"
    mdp.write_text("\n".join(md) + "\n")
    return jsonl, mdp
