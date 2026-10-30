"""Characterization of the component palette and the objective presets (plan-phase4c).

``fgen sweep palette [--measure]`` writes ``characterization/palette.{jsonl,md}``: the
3:2 circuit chain, red and green science, plastic and sulfuric acid with the **full
palette** (every inserter, pole, belt and underground belt of the data; no tier hint)
under the three presets, from the anytime driver (deterministic time box, so the rows
reproduce), plus the proving driver for ``compact`` on the circuit chain, once with the
full palette and once without the vocabulary phase 4a never had (medium/big poles,
substations, undergrounds). Without ``--measure`` earlier measurements are kept while
the blueprint is unchanged, like the other sweeps.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from fgen.data.runtime import RuntimeState
from fgen.data.schema import GameData
from fgen.gen.base import Infeasible
from fgen.gen.direct_chain import DirectChainParams, generate, palette, predict
from fgen.gen.direct_chain_sweep import _cells, _key, load_previous
from fgen.gen.fluid_sweep import CHARS
from fgen.gen.sweep import Measurer, _jsonable, blueprint_sha, format_interface, runtime_section
from fgen.layout.render import LEGEND, to_ascii
from fgen.solve import cpsat

PRESET_NAMES = ("compact", "cheap", "simple")
BOX = 60.0  # seconds: the anytime time box of every row (deterministic budget)
GREEN_BOX = 300.0  # green science: its first full-palette period takes ~270 s (4c record)
PROVE_BOX = 3600.0
CP = "chemical-plant"
BASES = {
    "circuit-r2": DirectChainParams(target_rate=2.0, backend="cpsat", time_limit=BOX),
    "red-r1": DirectChainParams(
        recipe="automation-science-pack", target_rate=1.0, backend="cpsat", time_limit=BOX
    ),
    "green-r0.5": DirectChainParams(
        recipe="logistic-science-pack",
        target_rate=0.5,
        backend="cpsat",
        belt_inputs=("electronic-circuit",),
        max_lane_consumers=6,
        time_limit=GREEN_BOX,
    ),
    "plastic-r2": DirectChainParams(
        recipe="plastic-bar", machine=CP, target_rate=2.0, backend="cpsat", time_limit=BOX
    ),
    "acid-r50": DirectChainParams(
        recipe="sulfuric-acid", machine=CP, target_rate=50.0, backend="cpsat", time_limit=BOX
    ),
}
DEVIATION_NOTE = 0.02


@dataclass(frozen=True)
class Case:
    name: str
    params: DirectChainParams


def phase_4a_vocabulary(gd: GameData) -> tuple[str, ...]:
    """Palette entries beyond what phase 4a could place: every pole but the circuit
    chain's own, and every underground belt (exclusions of the second proving row)."""
    pal = palette(BASES["circuit-r2"], gd)
    return (*pal["poles"][1:], *pal["undergrounds"])


def cases(gd: GameData) -> list[Case]:
    out = []
    for name, base in BASES.items():
        for preset in PRESET_NAMES:
            out.append(Case(f"{name}-{preset}", replace(base, preset=preset)))  # type: ignore[arg-type]
    circ = replace(BASES["circuit-r2"], prove=True, time_limit=PROVE_BOX)
    out.append(Case("circuit-r2-compact-prove", circ))
    out.append(Case("circuit-r2-compact-prove-4a", replace(circ, exclude=phase_4a_vocabulary(gd))))
    return out


def sweep(
    gd: GameData, measure: Measurer | None = None, previous: dict[str, dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    previous = previous or {}
    rows = []
    for c in cases(gd):
        row: dict[str, Any] = {
            "case": c.name,
            "params": asdict(c.params),
            "predicted": None,
            "measured": None,
        }
        # prove rows: the proof driver's side-by-side models share the box, so its wall
        # cap grows with the oversubscription (plan-phase7 B; params, and so the keys
        # of earlier measurements, stay as they were)
        cpsat.PROVE_WALL = cpsat.oversubscription() if c.params.prove else 1.0
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
        row["render"] = to_ascii(unit.layout, gd, chars=CHARS)
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
    cpsat.PROVE_WALL = 1.0
    return rows


def write(gd: GameData, out_dir: Path, measure: Measurer | None = None) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    jsonl = out_dir / "palette.jsonl"
    rows = sweep(gd, measure, load_previous(jsonl))
    jsonl.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
    md = [
        "# Component palette and objective presets: characterization",
        "",
        "Generated by `uv run --extra solver fgen sweep palette`; do not edit by hand. "
        f"Game data: {gd.source} {gd.version}. Every row has the full palette (no tier "
        "hint); `compact` = area, poles, inserters; `cheap` = entity cost (raw units through "
        "the recipes, `fgen.data.cost`) first; `simple` = fewest entity kinds first. Anytime "
        f"rows: {BOX:g} s box (green science {GREEN_BOX:g} s; deterministic budget); `prove` "
        "rows: the proving driver. "
        "Experiment log: `docs/experiments/4c-anytime-palette.md`.",
        "",
        LEGEND + "  u pipe-to-ground",
        "",
        "## Sweep",
        "",
        _cells(
            "case",
            "preset",
            "result",
            "periods",
            "period WxH",
            "unit area",
            "machines",
            "ins/poles/pipes per period",
            "cost per period (raw units)",
            "kinds",
            "entities (period)",
            "first feasible s",
            "solve s (proven)",
            "measured/s",
        ),
        _cells(*["---"] * 14),
    ]
    for r in rows:
        p = r["params"]
        head = [r["case"], p["preset"]]
        if r["predicted"] is None:
            md.append(_cells(*head, f"infeasible: {r['infeasible']['reason'][:90]}", *[""] * 11))
            continue
        q, per, m = r["predicted"], r["period"], r["measured"]
        mt = per.get("metrics") or {}
        cost = mt.get("cost_units")
        machines = {"assembling-machine-1", "assembling-machine-2", CP}
        ents = ", ".join(e for e in per.get("entities", []) if e not in machines)
        md.append(
            _cells(
                *head,
                "ok",
                r["periods"],
                f"{per['width']}x{per['height']}",
                q["area"],
                q["machines"],
                f"{per['inserters']}/{per['poles']}/{per.get('pipes', 0)}",
                f"{cost:g}" if cost is not None else "-",
                mt.get("kinds", "-"),
                ents,
                f"{per['phases'].get('first_feasible_s', 0):.1f}",
                f"{per['seconds']:.1f} ({'yes' if per['proven'] else 'no'})",
                f"{m.get('throughput_mean', m['throughput']):.4g}" if m else "-",
            )
        )
    md += ["", "## Units", ""]
    for r in rows:
        if r.get("render"):
            md += [f"### {r['case']}", "", "```", r["render"], *r["interfaces"], "```", ""]
    md += _measured(rows)
    mdp = out_dir / "palette.md"
    mdp.write_text("\n".join(md) + "\n")
    return jsonl, mdp


def _measured(rows: list[dict[str, Any]]) -> list[str]:
    meas = [r for r in rows if r.get("measured")]
    if not meas:
        return []
    out = [
        "## Measured",
        "",
        "Built from the blueprint in Factorio headless (full research, recipes unlocked), "
        "fixtures as in `characterization/direct_chain.md` and `fluid_chain.md`; whole-window "
        "mean after the chain warm-up; `predicted/s` under the measured force state.",
        "",
        "| case | target/s | predicted/s | measured/s | Δ vs predicted | note |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for r in meas:
        m, t = r["measured"], r["params"]["target_rate"]
        pred = m.get("predicted_throughput", t)
        got = m.get("throughput_mean", m["throughput"])
        delta = (got - pred) / pred
        note = (
            "below prediction"
            if delta < -DEVIATION_NOTE
            else ("below target" if got + 1e-9 < t else "")
        )
        out.append(f"| {r['case']} | {t:g} | {pred:g} | {got:.4g} | {delta:+.1%} | {note} |")
    return [*out, "", *runtime_section(rows, "direct_chain.predict")]
