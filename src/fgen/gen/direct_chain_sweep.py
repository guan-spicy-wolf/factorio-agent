"""Characterization of the direct-insertion chain (plan-phase4a §4).

``fgen sweep direct-chain [--measure]`` writes ``characterization/direct_chain.{jsonl,md}``:
solver vs hand-written baseline for R = 1, 2, 4 products/s, a few tier variants and an
infeasible case. Without ``--measure`` earlier measurements are kept while the unit's
blueprint is unchanged (``measured.blueprint_sha``), like the other sweeps.
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
from fgen.gen.sweep import Measurer, _jsonable, blueprint_sha, format_interface, runtime_section
from fgen.layout.render import LEGEND, to_ascii
from fgen.solve.period import tile

RATES = (1.0, 2.0, 4.0)
# the phase-4a rows keep their backend; every existing row keeps the given tiers and the
# proving driver (plan-phase4c: characterization proves)
PHASE_4A = DirectChainParams(backend="exhaustive", palette="given", prove=True)
SCIENCE_RATES = (0.5, 1.0, 2.0)
RED = "automation-science-pack"
GREEN = "logistic-science-pack"
GREEN_RATES = (0.5, 1.0)
GREEN_TIME_LIMIT = 2400.0  # seconds for the one green period (proved in ~31 min)
LONG = "long-handed-inserter"
DEVIATION_NOTE = 0.02  # relative measured/target gap that is called out in the .md


@dataclass(frozen=True)
class Case:
    name: str
    params: DirectChainParams


def cases() -> list[Case]:
    out = []
    for r in RATES:
        for lay in ("solver", "baseline"):
            out.append(Case(f"{lay}-r{r:g}", replace(PHASE_4A, target_rate=r, layout=lay)))  # type: ignore[arg-type]
    d = PHASE_4A
    for lay in ("solver", "baseline"):
        out += [
            Case(
                f"{lay}-am2-r3",
                replace(d, target_rate=3.0, machine="assembling-machine-2", layout=lay),
            ),  # type: ignore[arg-type]
            Case(f"{lay}-medium-r2", replace(d, pole="medium-electric-pole", layout=lay)),  # type: ignore[arg-type]
            Case(f"{lay}-basic-r2", replace(d, inserter="inserter", layout=lay)),  # type: ignore[arg-type]
        ]
    # the solver may mix in long-handed inserters (the baseline row cannot use them)
    for r in (2.0, 4.0):
        long = replace(d, target_rate=r, extra_inserters=(LONG,))
        out.append(Case(f"solver-fastlong-r{r:g}", long))
        out.append(Case(f"baseline-fastlong-r{r:g}", replace(long, layout="baseline")))
    # phase 4b: red science on the CP-SAT backend, period kinds chosen by machine count
    # (lanes win: exact ratio), the direct-insertion surplus periods for comparison, and
    # the same direct period from the exhaustive search (consumers capped at what it can
    # prove quickly)
    red = replace(d, recipe=RED, backend="cpsat")
    for r in SCIENCE_RATES:
        out.append(Case(f"red-r{r:g}", replace(red, target_rate=r)))
        out.append(Case(f"red-direct-r{r:g}", replace(red, target_rate=r, intermediates="direct")))
    out.append(
        Case(
            "red-direct-exhaustive-r1",
            replace(
                red, target_rate=1.0, intermediates="direct", backend="exhaustive", max_consumers=2
            ),
        )
    )
    # green science: circuits on a belt, inserters and belts on the lanes of one belt,
    # gears machine to machine; needs long-handed inserters. Six green machines per
    # period (0.5/s) bound the search (a 12-machine period would take hours); 1/s is
    # two such periods (the same period: one solve)
    green = replace(
        d,
        recipe=GREEN,
        backend="cpsat",
        belt_inputs=("electronic-circuit",),
        extra_inserters=(LONG,),
        max_lane_consumers=6,
        time_limit=GREEN_TIME_LIMIT,
    )
    for r in GREEN_RATES:
        out.append(Case(f"green-r{r:g}", replace(green, target_rate=r)))
    out.append(Case("solver-r6", replace(d, target_rate=6.0)))
    out.append(Case("solver-r20", replace(d, target_rate=20.0)))
    return out


def warmup_s(unit: Any, gd: GameData, base: float) -> float:
    """Warm-up for a chain: ``base`` plus twice the belt transit along the unit (raw
    materials reach the last machine, products travel back out) plus two crafts of its
    slowest recipe (input buffers and the first outputs)."""
    w, _ = unit.layout.size(gd)
    speeds = [
        gd.entity(q.name).speed
        for q in unit.layout.placements
        if gd.entity(q.name).type == "transport-belt"
    ]
    tiles_s = min(speeds) * gd.constant("ticks_per_second")  # the slowest belt row
    craft = max(
        gd.recipe(q.extra["recipe"]).energy_required / gd.entity(q.name).crafting_speed
        for q in unit.layout.placements
        if q.extra.get("recipe")
    )
    return base + 2 * w / tiles_s + 2 * craft


def _key(params: dict[str, Any]) -> str:
    """Rows match earlier measurements on their params; the phase-4c fields at their
    legacy values are left out (rows written before they existed match)."""
    from fgen.gen.direct_chain import LEGACY

    kept = {k: v for k, v in params.items() if not (k in LEGACY and _norm(v) == LEGACY[k])}
    return json.dumps(kept, sort_keys=True)


def _norm(v: Any) -> Any:
    return list(v) if isinstance(v, tuple) else v


def load_previous(path: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                if r.get("measured"):
                    out[_key(r["params"])] = r["measured"]
    return out


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
        per = dict(unit.meta["period"])
        row["period"] = _jsonable(per)
        row["periods"] = unit.meta["periods"]
        row["ratio"] = unit.meta["ratio"]
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


def _render(gd: GameData, p: DirectChainParams) -> list[str]:
    unit = generate(p, gd)
    per_meta = unit.meta["period"]
    lines = [
        f"### {p.layout}, target {p.target_rate:g}/s",
        "",
        f"Period {per_meta['width']}x{per_meta['height']} (ratio "
        f"{':'.join(map(str, unit.meta['ratio']))}), {unit.meta['periods']} period(s).",
        "",
        "```",
        to_ascii(unit.layout, gd, rulers=True),
        "",
        *(format_interface(i) for i in unit.interfaces),
        "```",
        "",
    ]
    return lines


def _period_render(gd: GameData, p: DirectChainParams) -> str:
    from fgen.gen.direct_chain import period_for, plan

    pl = plan(p, gd)
    per, _ = period_for(pl, p, gd)
    return to_ascii(tile(per, 1), gd)


def write(gd: GameData, out_dir: Path, measure: Measurer | None = None) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    jsonl = out_dir / "direct_chain.jsonl"
    rows = sweep(gd, measure, load_previous(jsonl))
    jsonl.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
    md = [
        "# Direct-insertion chain: characterization",
        "",
        "Generated by `uv run fgen sweep direct-chain`; do not edit by hand.",
        f"Game data: {gd.source} {gd.version}. `solver` periods come from `fgen.solve.cycle` "
        "(exhaustive search, smallest area first); `baseline` is the hand-written classic "
        "row (`fgen.gen.direct_chain_baseline`). Experiment log: "
        "`docs/experiments/4a-cycle-solver.md`.",
        "",
        LEGEND,
        "",
        "## Periods (3:2, assembling-machine-1, fast inserter, small pole, yellow belt)",
        "",
    ]
    d = PHASE_4A
    for lay in ("solver", "baseline"):
        md += [f"`{lay}`:", "", "```", _period_render(gd, replace(d, layout=lay)), "```", ""]  # type: ignore[arg-type]
    md += ["## Units", ""]
    for r in RATES:
        md += _render(gd, replace(d, target_rate=r))
    md += [
        "## Sweep",
        "",
        _cells(
            "case",
            "machine",
            "inserter",
            "pole",
            "target/s",
            "result",
            "ratio",
            "periods",
            "period WxH",
            "unit area",
            "machines",
            "inserters/period",
            "poles/period",
            "solve s",
            "measured/s",
        ),
        _cells(*["---"] * 15),
    ]
    for r in rows:
        p = r["params"]
        head = [r["case"], p["machine"], p["inserter"], p["pole"], f"{p['target_rate']:g}"]
        if r["predicted"] is None:
            sugg = "; ".join(r["infeasible"]["suggestions"][:2])
            reason = r["infeasible"]["reason"][:80]
            md.append(_cells(*head, f"infeasible: {reason} (try {sugg})", *[""] * 9))
            continue
        q, per, m = r["predicted"], r["period"], r["measured"]
        md.append(
            _cells(
                *head,
                "ok",
                ":".join(map(str, r["ratio"])),
                r["periods"],
                f"{per['width']}x{per['height']}",
                q["area"],
                q["machines"],
                per["inserters"],
                per["poles"],
                f"{per['seconds']:.2f}" if per.get("seconds") is not None else "-",
                f"{m.get('throughput_mean', m['throughput']):.4g}" if m else "-",
            )
        )
    md += ["", *_compare(rows), *_measured(rows)]
    mdp = out_dir / "direct_chain.md"
    mdp.write_text("\n".join(md) + "\n")
    return jsonl, mdp


def _cells(*xs: Any) -> str:
    return "| " + " | ".join(str(x) for x in xs) + " |"


def _compare(rows: list[dict[str, Any]]) -> list[str]:
    by = {r["case"]: r for r in rows}
    out = [
        "## Solver vs hand-written baseline",
        "",
        _cells(
            "variant",
            "target/s",
            "solver area",
            "baseline area",
            "machines (s/b)",
            "inserters/period (s/b)",
            "poles/period (s/b)",
            "measured/s (s/b)",
        ),
        _cells(*["---"] * 8),
    ]

    def ms(r: dict[str, Any]) -> str:
        m = r["measured"]
        return f"{m.get('throughput_mean', m['throughput']):.4g}" if m else "-"

    for c in cases():
        if not c.name.startswith("solver-"):
            continue
        name = c.name.removeprefix("solver-")
        b, s = by.get("baseline-" + name), by[c.name]
        if b is None or s["predicted"] is None:
            continue
        rate = f"{c.params.target_rate:g}"
        if b["predicted"] is None:
            why = b["infeasible"]["reason"][:60]
            out.append(
                _cells(name, rate, s["predicted"]["area"], f"infeasible ({why})", "", "", "", "")
            )
            continue
        sp, bp = s["predicted"], b["predicted"]
        out.append(
            _cells(
                name,
                rate,
                sp["area"],
                bp["area"],
                f"{sp['machines']}/{bp['machines']}",
                f"{s['period']['inserters']}/{b['period']['inserters']}",
                f"{s['period']['poles']}/{b['period']['poles']}",
                f"{ms(s)}/{ms(b)}",
            )
        )
    return [*out, ""]


def _measured(rows: list[dict[str, Any]]) -> list[str]:
    meas = [r for r in rows if r.get("measured")]
    if not meas:
        return []
    m0 = meas[0]["measured"]
    out = [
        "## Measured",
        "",
        f"Factorio headless {m0.get('game_version', '?')}: unit built from its blueprint (recipes "
        "unlocked first, see VERIFY §11), iron and copper side-loaded onto their lanes by "
        "infinity-chest fixtures, product counted at a loader + chest, free power. Throughput "
        f"is the whole-window mean of {len(m0['samples'])} samples of {m0['sample_s']:g} s "
        "(`samples/s` lists them; a 10 s sample resolves one item) after a "
        "warm-up of 60 s + twice the belt transit along the unit + two crafts of its slowest "
        "recipe (`direct_chain_sweep.warmup_s`; long science chains need minutes to fill).",
        "",
        "| case | target/s | predicted/s | measured/s | Δ vs predicted | samples/s | "
        "machine states (probes) | note |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in meas:
        m, t = r["measured"], r["params"]["target_rate"]
        pred = m.get("predicted_throughput", (r.get("predicted") or {}).get("throughput", t))
        got = m.get("throughput_mean", m["throughput"])
        delta = (got - pred) / pred
        states = ", ".join(f"{k} {v}" for k, v in sorted(m["machine_status_probes"].items()))
        note = ""
        if delta < -DEVIATION_NOTE:
            note = f"below target; starved machines {m.get('starved_machines')}"
        elif "full_output" in m["machine_status_probes"]:
            note = "producers idle with full output: the period's ratio makes surplus intermediate"
        elif "item_ingredient_shortage" in m["machine_status_probes"]:
            note = "brief ingredient waits seen by the probes; no loss at the sample resolution"
        out.append(
            f"| {r['case']} | {t:g} | {pred:g} | {got:.4g} | {delta:+.1%} | "
            f"{' '.join(f'{x:g}' for x in m['samples'])} | {states} | {note} |"
        )
    return [*out, "", *runtime_section(rows, "direct_chain.predict")]
