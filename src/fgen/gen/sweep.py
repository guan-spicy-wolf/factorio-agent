"""Parameter sweep for characterization (plan §4.6).

``predicted`` comes from the analytic model; ``measured`` is filled by
``fgen sweep smelting --measure`` (a callable that deploys a unit in headless Factorio,
see ``fgen.sim.measure``). Without ``--measure`` previous measurements are kept as long
as the generated blueprint is unchanged (``measured.blueprint_sha``)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict, replace
from itertools import product
from pathlib import Path
from typing import Any

from fgen.blueprint import codec
from fgen.blueprint.export import unit_to_blueprint
from fgen.data.schema import GameData
from fgen.gen import throughput as tp
from fgen.gen.base import Infeasible
from fgen.gen.smelting_row import SmeltingRowParams, generate
from fgen.layout.model import Interface, Unit
from fgen.layout.render import LEGEND, to_ascii

FURNACES = ("stone-furnace", "steel-furnace", "electric-furnace")
POLES = ("small-electric-pole", "medium-electric-pole")
RATES = (5.0, 7.5, 15.0)


def _jsonable(x: Any) -> Any:
    if isinstance(x, dict):
        return {k: _jsonable(v) for k, v in x.items()}
    if isinstance(x, list | tuple):
        return [_jsonable(v) for v in x]
    return x


type Measurer = Callable[[Unit], dict[str, Any]]

DEVIATION_NOTE = 0.10  # relative predicted/measured gap that must be explained in the .md


def blueprint_sha(unit: Unit, gd: GameData) -> str:
    return hashlib.sha256(codec.encode(unit_to_blueprint(unit, gd)).encode()).hexdigest()[:16]


def _key(params: dict[str, Any]) -> str:
    return json.dumps(params, sort_keys=True)


def load_previous(path: Path) -> dict[str, dict[str, Any]]:
    """``params`` key -> measured dict, from an existing ``.jsonl``."""
    out: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            if r.get("measured"):
                out[_key(r["params"])] = r["measured"]
    return out


def sweep_smelting(
    gd: GameData,
    measure: Measurer | None = None,
    previous: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """One row per (furnace, pole, rate). Infeasible defaults are measured with their
    first suggestion applied (recorded as ``measured.params_changed``)."""
    rows = []
    previous = previous or {}
    for furnace, pole, rate in product(FURNACES, POLES, RATES):
        p = SmeltingRowParams(furnace=furnace, pole=pole, target_rate=rate)
        row: dict[str, Any] = {"params": asdict(p), "predicted": None, "measured": None}
        change: dict[str, Any] = {}
        try:
            unit = generate(p, gd)
            row["predicted"] = _jsonable(tp.predict(unit, gd))
        except Infeasible as exc:
            row["infeasible"] = {"reason": exc.reason, "suggestions": exc.suggestions}
            if not exc.changes:
                rows.append(row)
                continue
            change = dict(exc.changes[0])
            unit = generate(replace(p, **change), gd)
        sha = blueprint_sha(unit, gd)
        if measure is not None:
            m = dict(measure(unit))
            m["blueprint_sha"] = sha
            if change:
                m["params_changed"] = change
                pr = tp.predict(unit, gd)
                m["predicted_throughput"] = pr["throughput"]
                m["predicted_limits"] = pr["limits"]
            row["measured"] = _jsonable(m)
        else:
            old = previous.get(_key(row["params"]))
            if old and old.get("blueprint_sha") == sha:
                row["measured"] = old
        rows.append(row)
    return rows


def format_interface(i: Interface) -> str:
    rate = "" if i.rate is None else f" {i.rate:g}/s"
    lane = "" if i.lane is None else f" lane={i.lane}"
    head = (
        f"{i.role:<6} {i.kind:<5} {i.material or '-':<12} at ({i.tile.x},{i.tile.y}) "
        f"facing {i.direction.name}{lane}{rate}"
    )
    return "\n".join([head, *(f"         assumes: {a}" for a in i.assumptions)])


def describe(unit: Unit, gd: GameData) -> str:
    pred = tp.predict(unit, gd)
    limits = ", ".join(f"{k}={v:g}" for k, v in pred["limits"].items())
    lines = [
        to_ascii(unit.layout, gd, rulers=True),
        "",
        "interfaces:",
        *("  " + format_interface(i).replace("\n", "\n  ") for i in unit.interfaces),
        "",
        f"predict: {pred['machines']} x {unit.meta['machine']} "
        f"({pred['machine_rate']:g}/s each), {pred['width']}x{pred['height']} = "
        f"{pred['area']} tiles, throughput {pred['throughput']:g}/s "
        f"(target {pred['target_rate']:g}), bottleneck {pred['bottleneck']}",
        f"limits: {limits}",
    ]
    return "\n".join(lines)


def _render_section(gd: GameData, p: SmeltingRowParams) -> list[str]:
    out = [f"## target_rate = {p.target_rate:g}/s", ""]
    try:
        unit = generate(p, gd)
        note = None
    except Infeasible as exc:
        out += [f"Default parameters are infeasible: {exc.reason}.", ""]
        out += ["Suggestions: " + "; ".join(f"`{s}`" for s in exc.suggestions), ""]
        if not exc.changes:
            return out
        change = exc.changes[0]
        p = replace(p, **change)
        unit = generate(p, gd)
        note = ", ".join(f"{k}={v!r}" for k, v in change.items())
    if note:
        out += [f"Rendered with the first suggestion applied: `{note}`.", ""]
    params = ", ".join(f"{k}={v!r}" for k, v in asdict(p).items())
    out += [f"`{params}`", "", "```", describe(unit, gd), "```", ""]
    return out


def _compare_section(rows: list[dict[str, Any]]) -> list[str]:
    measured = [r for r in rows if r.get("measured")]
    if not measured:
        return []
    m0 = measured[0]["measured"]
    out = [
        "## Predicted vs measured",
        "",
        f"Measured in Factorio headless {m0.get('game_version', '?')} (base mod only) by "
        "`uv run fgen sweep smelting --measure`: unit placed from its blueprint, inputs fed "
        "by infinity chests through loaders (side-loaded onto the declared lanes), output "
        "counted at a loader + chest, free power. Throughput is the median over "
        f"{len(m0['samples'])} samples of {m0['sample_s']:g} s game time after "
        f"{m0['warmup_s']:g} s warm-up. *starved* lists furnaces (1 = upstream end) that "
        "reported `no_ingredients`/`no_fuel` in any probe.",
        "",
        "| furnace | pole | target/s | measured as | predicted/s | measured/s | Δ | "
        "samples/s | startup s | starved | note |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in measured:
        p, m = r["params"], r["measured"]
        pred = m.get("predicted_throughput", (r["predicted"] or {}).get("throughput"))
        meas = m["throughput"]
        delta = (meas - pred) / pred if pred else 0.0
        changed = m.get("params_changed")
        as_ = ", ".join(f"{k}={v}" for k, v in changed.items()) if changed else "default"
        startup = m["startup_ticks"] / m["ticks_per_second"] if m.get("startup_ticks") else None
        starved = m.get("starved_machines") or []
        if abs(delta) > DEVIATION_NOTE or meas < p["target_rate"]:
            note = explain(r)
        else:
            note = _tight_inputs(r) if starved else ""
        out.append(
            f"| {p['furnace']} | {p['pole']} | {p['target_rate']:g} | {as_} | {pred:g} | "
            f"{meas:g} | {delta:+.1%} | {' '.join(f'{x:g}' for x in m['samples'])} | "
            f"{'' if startup is None else f'{startup:.1f}'} | "
            f"{','.join(map(str, starved)) or '-'} | {note} |"
        )
    out.append("")
    return out


def _tight_inputs(row: dict[str, Any]) -> str:
    """Input limits equal to the predicted throughput (no headroom)."""
    m = row["measured"]
    limits = m.get("predicted_limits") or (row.get("predicted") or {}).get("limits") or {}
    pred = m.get("predicted_throughput", (row.get("predicted") or {}).get("throughput"))
    tight = [k for k, v in limits.items() if k.startswith("input:") and pred and v <= pred + 1e-9]
    if not tight:
        return "brief waits, below sample resolution"
    return (
        f"{', '.join(tight)} limit = {pred:g}/s = demand (no headroom): the last furnaces "
        "wait briefly; the loss is below the sample resolution"
    )


def explain(row: dict[str, Any]) -> str:
    """Diagnosis for a measurement that misses the target or the prediction by >10%."""
    m = row["measured"]
    starved = m.get("starved_machines") or []
    n = m.get("machines") or 0
    parts = []
    if not m.get("power", {}).get("harness_on_unit_network", True):
        parts.append("harness power not on the unit's network")
    if starved and n and min(starved) > n / 2:
        parts.append(
            f"furnaces {min(starved)}..{max(starved)} at the downstream end starve: the "
            "input lane delivers less than the furnaces take"
        )
    elif starved:
        parts.append(f"furnaces {','.join(map(str, starved))} starve")
    if not parts:
        parts.append("no starvation and power ok; model/measurement gap (see samples)")
    return "; ".join(parts)


def write_characterization(
    gd: GameData, out_dir: Path, measure: Measurer | None = None
) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    jsonl = out_dir / "smelting_row.jsonl"
    rows = sweep_smelting(gd, measure, load_previous(jsonl))
    jsonl.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))

    md = [
        "# Smelting row: characterization",
        "",
        "Generated by `uv run fgen sweep smelting`; do not edit by hand.",
        f"Game data: {gd.source} {gd.version} (extracted from `--dump-data`, see "
        "`docs/VERIFY.md`).",
        "`predicted` is the analytic model in `fgen.gen.throughput`; `measured` comes from",
        "headless Factorio (`--measure`, see the comparison table at the end).",
        "",
        "Coordinates: x east, y south, rows labelled on the left, x mod 10 on top.",
        "All inserters face N (pick up from the north, drop to the south).",
        "",
        LEGEND,
        "",
    ]
    for rate in RATES:
        md += _render_section(gd, SmeltingRowParams(target_rate=rate))
    md += [
        "## Sweep",
        "",
        "| furnace | pole | target/s | result | machines | area | throughput/s | bottleneck |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        p = r["params"]
        head = f"| {p['furnace']} | {p['pole']} | {p['target_rate']:g} |"
        if r["predicted"] is None:
            sugg = "; ".join(r["infeasible"]["suggestions"][:2])
            md.append(f"{head} infeasible (try {sugg}) | | | | |")
        else:
            q = r["predicted"]
            md.append(
                f"{head} ok | {q['machines']} | {q['area']} | {q['throughput']:g} | "
                f"{q['bottleneck']} |"
            )
    md.append("")
    md += _compare_section(rows)
    mdp = out_dir / "smelting_row.md"
    mdp.write_text("\n".join(md) + "\n")
    return jsonl, mdp
