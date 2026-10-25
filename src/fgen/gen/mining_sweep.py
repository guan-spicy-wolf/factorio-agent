"""Characterization of the mining-patch generator (plan-phase2 §3.4).

Painted cases (shapes from :mod:`fgen.terrain.paint` on the flat field) plus one row
for the real map (§3.5, filled by ``fgen sweep mining --real-map``). Each row holds the
contract (per output interface rate), and ``measured`` from headless Factorio when a
measurer is given; without one, previous measurements are kept while the blueprint is
unchanged. Writes ``characterization/mining_patch.{jsonl,md}``.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from fgen.data.runtime import RuntimeState
from fgen.data.schema import GameData
from fgen.gen.mining_patch import MiningPatchParams, generate, render
from fgen.gen.sweep import blueprint_sha
from fgen.layout.model import Unit
from fgen.layout.render import LEGEND, TERRAIN_LEGEND
from fgen.terrain.model import Patch, Terrain, patches
from fgen.terrain.paint import ShapeSpec

AMOUNT = 5000  # ore per painted tile: far more than a measurement mines
TOLERANCE = 0.02  # plan-phase4b §4 B: measured vs predicted per interface
REAL_MAP = "real-map"
BEFORE = "mining_patch.before.json"  # numbers before phase 6a, for the comparison


@dataclass(frozen=True)
class Case:
    name: str
    shape: ShapeSpec
    params: MiningPatchParams = field(default_factory=MiningPatchParams)
    note: str = ""


def _rect(x: int, y: int, w: int, h: int) -> ShapeSpec:
    return ShapeSpec("rectangle", {"x": x, "y": y, "w": w, "h": h, "amount": AMOUNT})


CASES: tuple[Case, ...] = (
    Case("rect-1belt", _rect(10, 10, 24, 8), MiningPatchParams(max_belts=1), "single belt"),
    Case("rect-2belt", _rect(10, 10, 24, 13), MiningPatchParams(max_belts=2), "two belts"),
    Case(
        "l-shape",
        ShapeSpec("l_shape", {"x": 10, "y": 10, "w": 24, "h": 20, "arm": 8, "amount": AMOUNT}),
    ),
    Case(
        "rect-hole",
        ShapeSpec(
            "rectangle", {"x": 10, "y": 10, "w": 24, "h": 18, "amount": AMOUNT}, ((18, 15, 8, 8),)
        ),
    ),
    Case(
        "blob",
        ShapeSpec("blob", {"seed": 7, "n_tiles": 300, "amount": AMOUNT, "cx": 30, "cy": 25}),
        note="random 4-connected blob",
    ),
    Case(
        "rect-axis-y",
        _rect(10, 10, 20, 24),
        MiningPatchParams(axis="y", pole="medium-electric-pole"),
        "belts flow south, medium poles",
    ),
    Case(
        "lane-capacity",
        _rect(10, 10, 66, 3),
        MiningPatchParams(pole="medium-electric-pole", max_belts=1),
        "long thin patch: drills cut at lane capacity",
    ),
    Case(
        "big-drill-medium",
        _rect(10, 10, 30, 24),
        MiningPatchParams(
            drill="big-mining-drill", pole="medium-electric-pole", belt="express-transport-belt"
        ),
        "5x5 drills, belt rows 11 apart > the medium pole's wire: rows bridged",
    ),
)

type Measurer = Callable[[Case, Unit, Terrain], dict[str, Any]]


def case_terrain(case: Case, margin: int = 12) -> tuple[Patch, Terrain]:
    t = Terrain.from_resources(case.shape.resources(), margin=margin)
    (p,) = patches(t, case.shape.resource)
    return p, t


def contract(unit: Unit) -> dict[str, Any]:
    outs = [i for i in unit.interfaces if i.kind == "item" and i.role == "output"]
    m = unit.meta
    return {
        "interfaces": [
            {
                "tile": [i.tile.x, i.tile.y],
                "direction": i.direction.name,
                "lane": i.lane,
                "rate": i.rate,
            }
            for i in outs
        ],
        "total": sum(i.rate or 0.0 for i in outs),
        "drills": m["anchor"]["drills"],
        "poles": m["poles"],
        "undergrounds": m.get("undergrounds", 0),
        "drills_before_poles": m.get("drills_before_poles"),
        "lanes": [b["lanes"] for b in m["belts"]],
        "dropped_by_reason": m["dropped_by_reason"],
        "suggestions": m["suggestions"],
        "anchor": m["anchor"],
        "drill_rate": m["drill_rate"],
        "lane_capacity": m["lane_capacity"],
        "drill_stacks": m.get("drill_stacks", False),
    }


def predicted(c: dict[str, Any], rs: RuntimeState) -> list[float]:
    """Per output belt under the force state ``rs``: every lane carries its drills'
    output with the mining productivity bonus, at most the lane capacity (the contract
    is the same with no research: drills per lane were cut to fit the lane). A drill
    that drops full belt stacks fills a lane with stacks of ``1 + belt_stack_size_bonus``
    items, so its lane holds that many times more."""
    rate = c["drill_rate"] * (1.0 + rs.mining_drill_productivity_bonus)
    cap = c["lane_capacity"] * ((1.0 + rs.belt_stack_size_bonus) if c.get("drill_stacks") else 1)
    return [sum(min(n * rate, cap) for n in lanes.values() if n) for lanes in c["lanes"]]


def compare(c: dict[str, Any], measured: dict[str, Any]) -> dict[str, Any]:
    """Per-interface deviation from the prediction under the force state the
    measurement ran with (``measured.runtime``), and from the no-research contract."""
    rs = RuntimeState.from_json(measured.get("runtime"))
    pred = predicted(c, rs) if "drill_rate" in c else [i["rate"] for i in c["interfaces"]]
    rates = [i["rate"] for i in c["interfaces"]]
    got = list(measured["sink_throughput"])
    devs = [(g - r) / r if r else 0.0 for g, r in zip(got, pred, strict=True)]
    vs_contract = [(g - r) / r if r else 0.0 for g, r in zip(got, rates, strict=True)]
    return {
        "predicted": [round(x, 6) for x in pred],
        "deviation": [round(d, 4) for d in devs],
        "deviation_vs_contract": [round(d, 4) for d in vs_contract],
        "max_abs_deviation": round(max((abs(d) for d in devs), default=0.0), 4),
        "within_tolerance": all(abs(d) <= TOLERANCE for d in devs),
    }


def _load_previous(path: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                out[r["case"]] = r
    return out


def sweep(
    gd: GameData,
    measure: Measurer | None = None,
    previous: dict[str, dict[str, Any]] | None = None,
    cases: tuple[Case, ...] = CASES,
) -> list[dict[str, Any]]:
    previous = previous or {}
    rows = []
    for case in cases:
        patch, terrain = case_terrain(case)
        unit = generate(case.params, gd, patch, terrain)
        sha = blueprint_sha(unit, gd)
        row: dict[str, Any] = {
            "case": case.name,
            "shape": case.shape.describe(),
            "note": case.note,
            "params": asdict(case.params),
            "blueprint_sha": sha,
            "contract": contract(unit),
            "measured": None,
        }
        if measure is not None:
            m = measure(case, unit, terrain)
            m.update(compare(row["contract"], m))
            row["measured"] = m
        else:
            old = previous.get(case.name)
            if old and old.get("blueprint_sha") == sha:
                row["measured"] = old.get("measured")
        rows.append(row)
    return rows


def _measured_cells(r: dict[str, Any]) -> list[str]:
    c, m = r["contract"], r["measured"]
    rates = " / ".join(f"{i['rate']:g}" for i in c["interfaces"])
    if not m:
        return [rates, "-", "-", "-", "-"]
    pred = " / ".join(f"{x:g}" for x in m.get("predicted", []))
    got = " / ".join(f"{x:g}" for x in m["sink_throughput"])
    devs = " / ".join(f"{d:+.1%}" for d in m["deviation"])
    status = ", ".join(f"{k} {v}" for k, v in sorted(m.get("machine_status_probes", {}).items()))
    return [rates, pred or "-", got, devs, status]


def _table(rows: list[dict[str, Any]]) -> list[str]:
    out = [
        "| case | shape | drills | belts | contract/s per belt (no research) | "
        "predicted/s (measured force state) | measured/s | Δ vs predicted | dropped | "
        "drill status probes |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        c = r["contract"]
        rates, pred, got, devs, status = _measured_cells(r)
        dropped = ", ".join(f"{k} {v}" for k, v in sorted(c["dropped_by_reason"].items())) or "-"
        out.append(
            f"| {r['case']} | {r['shape']} | {c['drills']} | {len(c['interfaces'])} | {rates} | "
            f"{pred} | {got} | {devs} | {dropped} | {status} |"
        )
    return out


def _runtime_section(rows: list[dict[str, Any]]) -> list[str]:
    """What the measured force state changed: per belt, contract (no research) ->
    prediction, and why."""
    meas = [r for r in rows if r.get("measured") and r["measured"].get("runtime")]
    if not meas:
        return []
    rs = RuntimeState.from_json(meas[0]["measured"]["runtime"])
    out = [
        "## Force state of the measurements",
        "",
        f"Every measurement ran after `force.research_all_technologies()`: {rs.describe()}. "
        "The layouts and contracts are generated for a force with nothing researched; the "
        "prediction (`mining_sweep.predicted`) multiplies each lane's drills by the drill "
        "rate x (1 + mining productivity) and caps it at the lane capacity (x (1 + belt "
        "stack bonus) for drills that drop full belt stacks).",
        "",
        "| case | belt | lanes (left, right drills) | contract/s | predicted/s | why |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for r in meas:
        c, m = r["contract"], r["measured"]
        for k, (i, p) in enumerate(zip(c["interfaces"], m.get("predicted", []), strict=False)):
            if abs(p - i["rate"]) < 1e-9:
                continue
            lanes = c["lanes"][k]
            cap = c["lane_capacity"]
            stacked = c.get("drill_stacks")
            why = []
            for ln, n in lanes.items():
                if not n:
                    continue
                raw = n * c["drill_rate"] * (1 + rs.mining_drill_productivity_bonus)
                lim = cap * ((1 + rs.belt_stack_size_bonus) if stacked else 1)
                why.append(
                    f"{ln}: {n} x {c['drill_rate']:g} x {1 + rs.mining_drill_productivity_bonus:g}"
                    f" = {raw:g}" + (f" > lane {lim:g}" if raw > lim + 1e-9 else "")
                )
            out.append(
                f"| {r['case']} | {k} | ({lanes.get('left', 0)}, {lanes.get('right', 0)}) | "
                f"{i['rate']:g} | {p:g} | {'; '.join(why)} |"
            )
    return [*out, ""]


def _real_map_section(r: dict[str, Any]) -> list[str]:
    c, m, info = r["contract"], r.get("measured"), r.get("real_map", {})
    a = c["anchor"]
    out = [
        "## Real map (end to end)",
        "",
        "Normal map generation (`config/ore-map-gen-settings.json`, fixed seed, default "
        "resources, no enemies). The terrain is read back with `RconTerrain.snapshot`, the "
        "nearest iron-ore patch (4-connected) is passed to the generator, the unit is built "
        "from its blueprint and measured like the painted cases. Produced by "
        "`uv run fgen sweep mining --real-map`.",
        "",
        f"- map: seed {info.get('seed')}, snapshot {info.get('snapshot_area')}, cleared "
        f"{info.get('cleared_entities', 0)} trees/rocks over the patch first",
        f"- patch: {r['shape']}",
        f"- params: `{', '.join(f'{k}={v!r}' for k, v in r['params'].items())}`",
        f"- drills {c['drills']}, poles {c['poles']}, belts {len(c['interfaces'])}, contract "
        f"{c['total']:g}/s, est. depletion {a['est_depletion_s'] / 3600:.1f} h (whole patch) / "
        f"{(a['est_depletion_covered_s'] or 0) / 3600:.1f} h (tiles under drills)",
        f"- dropped: {c['dropped_by_reason']}",
        "",
    ]
    if m:
        out += [
            f"Measured in {m.get('game_version', '?')}: total {sum(m['sink_throughput']):g}/s "
            f"vs contract {c['total']:g}/s; max |Δ| per belt {m['max_abs_deviation']:.1%}.",
            "",
        ]
    out += [*_table([r]), ""]
    if info.get("render"):
        out += ["```", info["render"], "```", ""]
    return out


def _before_after(path: Path, rows: list[dict[str, Any]]) -> list[str]:
    """Drills and ``pole_slot`` against the recorded numbers before phase 6a (poles took
    a whole drill slot), when ``characterization/mining_patch.before.json`` exists."""
    if not path.exists():
        return []
    before = json.loads(path.read_text())
    out = [
        "## Before vs after (phase 6a)",
        "",
        f"Before: {before['source']}. After: this sweep. `pole_slot` after = drills the "
        "rows hold without poles (after the lane cut) minus drills placed; before = drill "
        "grid slots a pole took.",
        "",
        "| case | drills before | drills after | pole_slot before | pole_slot after | "
        "poles before | poles after | undergrounds after |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    tot = [0, 0, 0, 0]
    for r in rows:
        b = before["cases"].get(r["case"])
        if b is None:
            continue
        c = r["contract"]
        ps = c["dropped_by_reason"].get("pole_slot", 0)
        tot = [tot[0] + b["drills"], tot[1] + c["drills"], tot[2] + b["pole_slot"], tot[3] + ps]
        out.append(
            f"| {r['case']} | {b['drills']} | {c['drills']} | {b['pole_slot']} | {ps} | "
            f"{b['poles']} | {c['poles']} | {c.get('undergrounds', '-')} |"
        )
    out += [f"| **total** | {tot[0]} | {tot[1]} | {tot[2]} | {tot[3]} | | | |", ""]
    return out


def write(
    gd: GameData,
    out_dir: Path,
    measure: Measurer | None = None,
    real_map_row: dict[str, Any] | None = None,
) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    jsonl = out_dir / "mining_patch.jsonl"
    previous = _load_previous(jsonl)
    rows = sweep(gd, measure, previous)
    real = real_map_row or previous.get(REAL_MAP)
    all_rows = rows + ([real] if real else [])
    jsonl.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in all_rows))

    md = [
        "# Mining patch: characterization",
        "",
        "Generated by `uv run fgen sweep mining [--measure] [--real-map]`; do not edit by hand.",
        f"Game data: {gd.source} {gd.version}. Contract = drills x `mining_speed / "
        "mining_time` per belt (mining productivity 0), from `fgen.gen.mining_patch`.",
        "",
        "Painted cases: ore painted on the flat test field (`RconTerrain.paint`, "
        f"{AMOUNT} per tile), unit built from its blueprint, each belt end feeds a loader + "
        "chest sink, free power. Measured per belt as the median of 20 s game-time samples "
        f"after a 10 s warm-up. Tolerance |Δ vs predicted| <= {TOLERANCE:.0%} per belt.",
        "",
        "Coordinates are world tiles (anchor unit). " + LEGEND,
        TERRAIN_LEGEND,
        "",
        "## Summary",
        "",
        *_table(rows),
        "",
        *_runtime_section(rows + ([real] if real else [])),
    ]
    for case in CASES:
        r = next(x for x in rows if x["case"] == case.name)
        patch, terrain = case_terrain(case)
        unit = generate(case.params, gd, patch, terrain)
        c = r["contract"]
        md += [
            f"## {case.name}",
            "",
            f"{r['shape']}{' — ' + case.note if case.note else ''}. "
            f"`{', '.join(f'{k}={v!r}' for k, v in r['params'].items())}`",
            "",
            "```",
            render(unit, gd, terrain),
            "```",
            "",
            f"drills {c['drills']}, poles {c['poles']}, lanes per belt "
            f"{[(b['left'], b['right']) for b in c['lanes']]} (left, right), dropped "
            f"{c['dropped_by_reason'] or '-'}"
            + (f"; suggestions: {', '.join(c['suggestions'])}" if c["suggestions"] else ""),
            "",
        ]
    md += _before_after(out_dir / BEFORE, all_rows)
    if real:
        md += _real_map_section(real)
    mdp = out_dir / "mining_patch.md"
    mdp.write_text("\n".join(md) + "\n")
    return jsonl, mdp
