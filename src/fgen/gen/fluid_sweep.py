"""Characterization of fluid machines on the direct_chain family (plan-phase5a).

``fgen sweep fluid [--measure]`` writes ``characterization/fluid_chain.{jsonl,md}``:
plastic (petroleum gas + coal on a belt) at 1, 2, 4 bars/s, its parameter variants
(mirror, pole, inserter tiers, no pipe-to-ground), and sulfuric acid (water in, acid out
on a pipe). A separate file from ``direct_chain`` so that regenerating it never re-solves
the 30-minute green-science period. Without ``--measure`` earlier measurements are kept
while the unit's blueprint is unchanged (``measured.blueprint_sha``), like every sweep.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from fgen.data.runtime import RuntimeState
from fgen.data.schema import GameData
from fgen.gen.base import Infeasible
from fgen.gen.direct_chain import DirectChainParams, generate, predict
from fgen.gen.direct_chain_sweep import _cells, _key, load_previous
from fgen.gen.sweep import Measurer, _jsonable, blueprint_sha, format_interface, runtime_section
from fgen.layout.model import Unit
from fgen.layout.render import LEGEND, to_ascii

CP = "chemical-plant"
LONG = "long-handed-inserter"
PLASTIC_RATES = (1.0, 2.0, 4.0)
ACID_RATES = (1.0, 2.0, 100.0)  # the plan's 1 and 2 units/s are 1/50 of one plant; 100 = two
# phase-5a rows: the given tiers, proved (plan-phase4c keeps them as they were)
PLASTIC = DirectChainParams(
    recipe="plastic-bar",
    machine=CP,
    backend="cpsat",
    extra_inserters=(LONG,),
    palette="given",
    prove=True,
)
ACID = DirectChainParams(
    recipe="sulfuric-acid", machine=CP, backend="cpsat", palette="given", prove=True
)
CHARS = {"pipe-to-ground": "u"}  # tell the underground ends from plain pipes
DEVIATION_NOTE = 0.02


@dataclass(frozen=True)
class Case:
    name: str
    params: DirectChainParams


def cases() -> list[Case]:
    out = [Case(f"plastic-r{r:g}", replace(PLASTIC, target_rate=r)) for r in PLASTIC_RATES]
    two = replace(PLASTIC, target_rate=2.0)
    out += [
        Case("plastic-mirror-r2", replace(two, mirror=True)),
        Case("plastic-medium-r2", replace(two, pole="medium-electric-pole")),
        Case("plastic-fastonly-r2", replace(two, extra_inserters=())),
        Case("plastic-basic-r2", replace(two, inserter="inserter")),
        Case("plastic-noptg-r2", replace(two, extra_inserters=(), pipe_to_ground=None)),
    ]
    out += [Case(f"acid-r{r:g}", replace(ACID, target_rate=r)) for r in ACID_RATES]
    return out


def warmup_s(unit: Unit, gd: GameData, base: float) -> float:
    from fgen.gen.direct_chain_sweep import warmup_s as chain_warmup

    return chain_warmup(unit, gd, base)


def sweep(
    gd: GameData, measure: Measurer | None = None, previous: dict[str, dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    previous = previous or {}
    rows = []
    for c in cases():
        row: dict[str, Any] = {
            "case": c.name,
            "params": asdict(c.params),
            "predicted": None,
            "measured": None,
        }
        try:
            unit = generate(c.params, gd)
        except Infeasible as exc:
            row["infeasible"] = {"reason": exc.reason, "suggestions": exc.suggestions}
            rows.append(row)
            continue
        row["predicted"] = _jsonable(predict(unit, gd))
        row["period"] = _jsonable(dict(unit.meta["period"]))
        row["periods"] = unit.meta["periods"]
        row["ratio"] = unit.meta["ratio"]
        row["interfaces"] = [format_interface(i) for i in unit.interfaces]
        row["plants"] = [
            [p.direction.name, bool(p.extra.get("mirror"))]
            for p in unit.layout.placements
            if p.name == CP
        ]
        sha = blueprint_sha(unit, gd)
        if measure is not None:
            try:
                m = dict(measure(unit))
            except Exception as exc:  # a harness that cannot be wired: record, go on
                row["measure_error"] = f"{type(exc).__name__}: {exc}"
                rows.append(row)
                continue
            m["blueprint_sha"] = sha
            pr = predict(unit, gd, RuntimeState.from_json(m.get("runtime")))
            m["predicted_throughput"] = pr["throughput"]
            m["predicted_limits"] = pr["limits"]
            row["measured"] = _jsonable(m)
        else:
            old = previous.get(_key(row["params"]))
            if old and old.get("blueprint_sha") == sha:
                row["measured"] = old
        rows.append(row)
    return rows


def _unit_block(gd: GameData, c: Case) -> list[str]:
    unit = generate(c.params, gd)
    per = unit.meta["period"]
    poses = ", ".join(
        f"{p.direction.name}{' mirrored' if p.extra.get('mirror') else ''}"
        for p in unit.layout.placements
        if p.name == CP
    )
    return [
        f"### {c.name}: {c.params.recipe}, target {c.params.target_rate:g}/s",
        "",
        f"Period {per['width']}x{per['height']} x {unit.meta['periods']}, "
        f"{per['inserters']} inserters, {per['poles']} poles, {per.get('pipes', 0)} pipe "
        f"entities per period; plants facing {poses}; solved in {per['seconds']:.1f} s "
        f"({'proved smallest' if not per['timed_out'] else 'time limit: not proved'}).",
        "",
        "```",
        to_ascii(unit.layout, gd, rulers=True, chars=CHARS),
        "",
        *(format_interface(i) for i in unit.interfaces),
        "```",
        "",
    ]


def write(gd: GameData, out_dir: Path, measure: Measurer | None = None) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    jsonl = out_dir / "fluid_chain.jsonl"
    rows = sweep(gd, measure, load_previous(jsonl))
    jsonl.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
    md = [
        "# Fluid machines (direct_chain family): characterization",
        "",
        "Generated by `uv run fgen sweep fluid`; do not edit by hand. "
        f"Game data: {gd.source} {gd.version}. Periods from the CP-SAT backend with the fluid "
        "vocabulary (`fgen.solve.cpsat_fluid`: pipes and pipe-to-ground pairs on any free "
        "tile, machine directions, one network per fluid, no unintended adjacency). "
        "Experiment log: `docs/experiments/5a-fluids.md`.",
        "",
        LEGEND + "  u pipe-to-ground",
        "",
        "## Units",
        "",
    ]
    for c in cases():
        if c.name in ("plastic-r1", "plastic-r4", "plastic-fastonly-r2", "acid-r1", "acid-r100"):
            md += _unit_block(gd, c)
    md += [
        "## Sweep",
        "",
        _cells(
            "case",
            "recipe",
            "inserters",
            "pole",
            "mirror",
            "target/s",
            "result",
            "periods",
            "period WxH",
            "unit area",
            "machines",
            "ins/poles/pipes per period",
            "solve s (proved)",
            "measured/s",
        ),
        _cells(*["---"] * 14),
    ]
    for r in rows:
        p = r["params"]
        tiers = "+".join([p["inserter"], *p["extra_inserters"]])
        head = [r["case"], p["recipe"], tiers, p["pole"], p["mirror"], f"{p['target_rate']:g}"]
        if r["predicted"] is None:
            reason = r["infeasible"]["reason"][:80]
            md.append(_cells(*head, f"infeasible: {reason}", *[""] * 7))
            continue
        q, per, m = r["predicted"], r["period"], r["measured"]
        md.append(
            _cells(
                *head,
                "ok",
                r["periods"],
                f"{per['width']}x{per['height']}",
                q["area"],
                q["machines"],
                f"{per['inserters']}/{per['poles']}/{per.get('pipes', 0)}",
                f"{per['seconds']:.1f} ({'no' if per['timed_out'] else 'yes'})",
                f"{m.get('throughput_mean', m['throughput']):.4g}" if m else "-",
            )
        )
    md += ["", *_measured(rows)]
    mdp = out_dir / "fluid_chain.md"
    mdp.write_text("\n".join(md) + "\n")
    return jsonl, mdp


def _measured(rows: list[dict[str, Any]]) -> list[str]:
    meas = [r for r in rows if r.get("measured")]
    if not meas:
        return []
    m0 = meas[0]["measured"]
    out = [
        "## Measured",
        "",
        f"Factorio headless {m0.get('game_version', '?')}: unit built from its blueprint "
        "(recipes unlocked, full research), items from infinity-chest fixtures, the input "
        "fluid from an infinity-pipe routed to the unit's port, plastic counted at a loader + "
        "chest, acid at a storage tank whose segment the harness adds up and empties every "
        "tick (VERIFY §13). Throughput is the whole-window mean of "
        f"{len(m0['samples'])} samples of {m0['sample_s']:g} s after the chain warm-up "
        "(`direct_chain_sweep.warmup_s`). `predicted/s` is `direct_chain.predict` under the "
        "measured force state (full research gives plastic +10% productivity).",
        "",
        "| case | target/s | contract output/s | predicted/s | measured/s | Δ vs predicted | "
        "samples/s | machine states (probes) | note |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in meas:
        m, t = r["measured"], r["params"]["target_rate"]
        pred = m.get("predicted_throughput", (r.get("predicted") or {}).get("throughput", t))
        got = m.get("throughput_mean", m["throughput"])
        contract = (r.get("predicted") or {}).get("throughput", t)
        delta = (got - pred) / pred
        states = ", ".join(f"{k} {v}" for k, v in sorted(m["machine_status_probes"].items()))
        note = ""
        if delta < -DEVIATION_NOTE:
            note = f"below prediction; starved machines {m.get('starved_machines')}"
        elif got + 1e-9 < t:
            note = "below target"
        out.append(
            f"| {r['case']} | {t:g} | {contract:g} | {pred:g} | {got:.4g} | {delta:+.1%} | "
            f"{' '.join(f'{x:g}' for x in m['samples'])} | {states} | {note} |"
        )
    return [*out, "", *runtime_section(rows, "direct_chain.predict")]
