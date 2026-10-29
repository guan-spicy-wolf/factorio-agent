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

from fgen.data.schema import GameData
from fgen.gen.mining_patch import MiningPatchParams, generate, render
from fgen.gen.sweep import blueprint_sha
from fgen.layout.model import Unit
from fgen.layout.render import LEGEND, TERRAIN_LEGEND
from fgen.terrain.model import Patch, Terrain, patches
from fgen.terrain.paint import ShapeSpec

AMOUNT = 5000  # ore per painted tile: far more than a measurement mines
TOLERANCE = 0.05  # plan-phase2 §4 M4: measured vs contract per interface
REAL_MAP = "real-map"


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
        "lanes": [b["lanes"] for b in m["belts"]],
        "dropped_by_reason": m["dropped_by_reason"],
        "suggestions": m["suggestions"],
        "anchor": m["anchor"],
    }


def compare(c: dict[str, Any], measured: dict[str, Any]) -> dict[str, Any]:
    """Per-interface deviation (measured / contract - 1)."""
    rates = [i["rate"] for i in c["interfaces"]]
    got = list(measured["sink_throughput"])
    devs = [(g - r) / r if r else 0.0 for g, r in zip(got, rates, strict=True)]
    return {
        "deviation": [round(d, 4) for d in devs],
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
        return [rates, "-", "-", "-"]
    got = " / ".join(f"{x:g}" for x in m["sink_throughput"])
    devs = " / ".join(f"{d:+.1%}" for d in m["deviation"])
    status = ", ".join(f"{k} {v}" for k, v in sorted(m.get("machine_status_probes", {}).items()))
    return [rates, got, devs, status]


def _table(rows: list[dict[str, Any]]) -> list[str]:
    out = [
        "| case | shape | drills | belts | contract/s per belt | measured/s | Δ | dropped | "
        "drill status probes |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        c = r["contract"]
        rates, got, devs, status = _measured_cells(r)
        dropped = ", ".join(f"{k} {v}" for k, v in sorted(c["dropped_by_reason"].items())) or "-"
        out.append(
            f"| {r['case']} | {r['shape']} | {c['drills']} | {len(c['interfaces'])} | {rates} | "
            f"{got} | {devs} | {dropped} | {status} |"
        )
    return out


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


BEFORE_FILE = "mining_patch_before.jsonl"  # snapshot of the rows before pole gaps (task 1)


def _before_after(rows: list[dict[str, Any]], before: dict[str, dict[str, Any]]) -> list[str]:
    """Drills, poles and ``pole_slot`` losses per case, before (snapshot) vs now."""
    if not before:
        return []

    def slots(c: dict[str, Any]) -> int:
        return int(c["dropped_by_reason"].get("pole_slot", 0))

    out = [
        "## Before vs after (pole placement)",
        "",
        "Before: the pole took a whole drill slot (`pole_slot`). After: the pole sits in a "
        "1-tile gap between two drills; `pole_slot` now counts only drills whose footprint "
        "no pole's supply area covers. Before rows: `characterization/mining_patch_before.jsonl`.",
        "",
        "| case | drills before → after | poles before → after | pole_slot before → after |",
        "| --- | --- | --- | --- |",
    ]
    tot = [0, 0, 0, 0]
    for r in rows:
        b = before.get(r["case"])
        if not b:
            continue
        bc, c = b["contract"], r["contract"]
        out.append(
            f"| {r['case']} | {bc['drills']} → {c['drills']} | {bc['poles']} → {c['poles']} | "
            f"{slots(bc)} → {slots(c)} |"
        )
        tot = [tot[0] + bc["drills"], tot[1] + c["drills"], tot[2] + slots(bc), tot[3] + slots(c)]
    out.append(f"| total | {tot[0]} → {tot[1]} | - | {tot[2]} → {tot[3]} |")
    return [*out, ""]


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
        f"after a 10 s warm-up. Tolerance |Δ| <= {TOLERANCE:.0%} per belt.",
        "",
        "Coordinates are world tiles (anchor unit). " + LEGEND,
        TERRAIN_LEGEND,
        "",
        "## Summary",
        "",
        *_table(rows),
        "",
        *_before_after(all_rows, _load_previous(out_dir / BEFORE_FILE)),
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
    if real:
        md += _real_map_section(real)
    mdp = out_dir / "mining_patch.md"
    mdp.write_text("\n".join(md) + "\n")
    return jsonl, mdp
