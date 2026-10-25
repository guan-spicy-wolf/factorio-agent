"""Command line entry point: ``uv run fgen <cmd>`` (plan §4.8)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from fgen.blueprint import codec
from fgen.blueprint.export import unit_to_blueprint
from fgen.blueprint.import_ import blueprint_to_layout
from fgen.data.loader import load_dump, load_seed
from fgen.data.schema import GameData
from fgen.gen import throughput as tp
from fgen.gen.base import Infeasible
from fgen.gen.smelting_row import SmeltingRowParams, generate
from fgen.gen.sweep import describe, write_characterization
from fgen.layout.render import LEGEND, to_ascii
from fgen.layout.validate import validate


def _read_bp_arg(arg: str) -> str:
    p = Path(arg)
    if len(arg) < 4096 and p.exists():
        return p.read_text().strip()
    return arg.strip()


def _gd(args: argparse.Namespace) -> GameData:
    return load_dump(args.data) if getattr(args, "data", None) else load_seed()


def cmd_decode(args: argparse.Namespace) -> int:
    print(json.dumps(codec.decode(_read_bp_arg(args.blueprint)), indent=2, ensure_ascii=False))
    return 0


def cmd_encode(args: argparse.Namespace) -> int:
    print(codec.encode(json.loads(Path(args.json_file).read_text())))
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    gd = _gd(args)
    layout = blueprint_to_layout(codec.decode(_read_bp_arg(args.blueprint)))
    print(to_ascii(layout, gd, rulers=args.rulers))
    print(LEGEND)
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    gd = _gd(args)
    layout = blueprint_to_layout(codec.decode(_read_bp_arg(args.blueprint)))
    vs = validate(layout, gd)
    for v in vs:
        tiles = " ".join(f"({t.x},{t.y})" for t in sorted(v.tiles)[:6])
        print(f"{v.code}: {v.message} {tiles}".rstrip())
    print(f"{len(vs)} violation(s)")
    return 1 if vs else 0


def _smelting_params(args: argparse.Namespace) -> SmeltingRowParams:
    d = SmeltingRowParams()
    return SmeltingRowParams(
        recipe=args.recipe or d.recipe,
        furnace=args.furnace,
        target_rate=args.rate if args.rate is not None else d.target_rate,
        belt=args.belt or d.belt,
        inserter=args.inserter or d.inserter,
        pole=args.pole or d.pole,
        sides=args.sides,
        fuel_lane=not args.no_fuel_lane,
        fuel=args.fuel,
    )


def _mining_params(args: argparse.Namespace):  # -> (Case, MiningPatchParams)
    from dataclasses import replace

    from fgen.gen.mining_sweep import CASES

    case = next((c for c in CASES if c.name == args.case), None)
    if case is None:
        raise ValueError(f"unknown case {args.case!r}; one of {[c.name for c in CASES]}")
    over = {
        k: v
        for k, v in {
            "resource": args.resource,
            "drill": args.drill,
            "belt": args.belt,
            "pole": args.pole,
            "axis": args.axis,
            "flow": args.flow,
            "max_belts": args.max_belts,
            "min_ore_tiles_per_drill": args.min_ore,
        }.items()
        if v is not None
    }
    return case, replace(case.params, **over)


def cmd_gen_mining(args: argparse.Namespace) -> int:
    from fgen.gen.mining_patch import generate as generate_mining
    from fgen.gen.mining_patch import render
    from fgen.gen.mining_sweep import case_terrain
    from fgen.gen.sweep import format_interface
    from fgen.layout.render import TERRAIN_LEGEND

    gd = _gd(args)
    case, params = _mining_params(args)
    patch, terrain = case_terrain(case)
    try:
        unit = generate_mining(params, gd, patch, terrain)
    except Infeasible as exc:
        print(f"infeasible: {exc.reason}", file=sys.stderr)
        for s in exc.suggestions:
            print(f"  try: {s}", file=sys.stderr)
        return 2
    m = unit.meta
    if args.json:
        meta = {k: v for k, v in m.items() if k != "dropped_drills"}
        print(json.dumps(meta, default=str, indent=2))
    else:
        print(f"{case.shape.describe()}: {patch.describe()}")
        print(render(unit, gd, terrain))
        print(LEGEND)
        print(TERRAIN_LEGEND)
        print("interfaces:")
        for i in unit.interfaces:
            print("  " + format_interface(i).replace("\n", "\n  "))
        a = m["anchor"]
        hours = a["est_depletion_s"] / 3600
        print(
            f"drills {a['drills']} x {m['drill_rate']:g}/s = {a['rate']:g}/s, poles {m['poles']}, "
            f"dropped {m['dropped_by_reason'] or '-'}, depletion ~{hours:.1f} h"
        )
        for s in m["suggestions"]:
            print(f"  suggestion: {s}")
        print()
    print(codec.encode(unit_to_blueprint(unit, gd, label=f"mining {case.name}")))
    return 0


def cmd_gen_direct_chain(args: argparse.Namespace) -> int:
    from fgen.gen.direct_chain import DirectChainParams
    from fgen.gen.direct_chain import generate as generate_chain
    from fgen.gen.direct_chain import predict as predict_chain
    from fgen.gen.sweep import format_interface

    gd = _gd(args)
    d = DirectChainParams()
    params = DirectChainParams(
        recipe=args.recipe or d.recipe,
        target_rate=args.rate if args.rate is not None else d.target_rate,
        machine=args.machine,
        inserter=args.inserter or d.inserter,
        belt=args.belt or d.belt,
        pole=args.pole or d.pole,
        layout=args.layout,
        time_limit=args.time_limit,
        extra_inserters=tuple(x for x in (args.extra_inserters or "").split(",") if x),
        backend=args.backend or d.backend,
        mirror=args.mirror,
    )
    try:
        unit = generate_chain(params, gd)
    except Infeasible as exc:
        print(f"infeasible: {exc.reason}", file=sys.stderr)
        for s in exc.suggestions:
            print(f"  try: {s}", file=sys.stderr)
        return 2
    pred = predict_chain(unit, gd)
    per = unit.meta["period"]
    if args.json:
        print(json.dumps({"predict": pred, "period": per}, default=str, indent=2))
    else:
        print(to_ascii(unit.layout, gd, rulers=True))
        print()
        print("interfaces:")
        for i in unit.interfaces:
            print("  " + format_interface(i).replace("\n", "\n  "))
        ratio = ":".join(map(str, unit.meta["ratio"]))
        print(
            f"period {per['width']}x{per['height']} ({per['layout']}, {ratio} "
            f"{' : '.join(unit.meta['stages'])}) x {unit.meta['periods']}; "
            f"{pred['machines']} machines, {pred['width']}x{pred['height']} = {pred['area']} "
            f"tiles, {pred['throughput']:g}/s (target {params.target_rate:g})"
        )
        if per["layout"] == "solver":
            print(f"solver: {per['seconds']:.2f} s, nodes {per['nodes']}, pruned {per['pruned']}")
        print(LEGEND)
        print()
    label = f"direct-chain {params.recipe} {params.target_rate:g}/s"
    print(codec.encode(unit_to_blueprint(unit, gd, label=label)))
    return 0


def cmd_gen(args: argparse.Namespace) -> int:
    if args.family == "mining":
        return cmd_gen_mining(args)
    if args.family == "direct-chain":
        return cmd_gen_direct_chain(args)
    gd = _gd(args)
    params = _smelting_params(args)
    try:
        unit = generate(params, gd)
    except Infeasible as exc:
        print(f"infeasible: {exc.reason}", file=sys.stderr)
        for s in exc.suggestions:
            print(f"  try: {s}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps({"predict": tp.predict(unit, gd)}, default=str, indent=2))
    else:
        print(describe(unit, gd))
        print(LEGEND)
        print()
    label = f"smelting {params.recipe} {params.target_rate:g}/s"
    print(codec.encode(unit_to_blueprint(unit, gd, label=label)))
    return 0


def cmd_sweep_mining(args: argparse.Namespace) -> int:
    from fgen.gen import mining_sweep

    gd = _gd(args)
    out = Path(args.out_dir)
    real = None
    if args.real_map:
        import json as _json

        from fgen.sim import mining
        from fgen.sim.server import REPO_CONFIG, HeadlessServer

        seed = _json.loads((REPO_CONFIG / mining.ORE_MAP).read_text()).get("seed")
        with HeadlessServer(map_gen_settings=mining.ORE_MAP) as srv:
            real = mining.real_map_row(srv, gd, seed=seed)
        print(
            f"real map: {real['shape']}; measured {real['measured']['sink_throughput']}",
            file=sys.stderr,
        )
    if args.measure:
        from fgen.sim import mining
        from fgen.sim.server import HeadlessServer

        with _server(args) as srv:
            measure = mining.painted_measurer(srv, gd)

            def logged(case, unit, terrain):
                m = measure(case, unit, terrain)
                print(f"measured {case.name}: {m['sink_throughput']}", file=sys.stderr)
                return m

            jsonl, md = mining_sweep.write(gd, out, logged, real)
    else:
        jsonl, md = mining_sweep.write(gd, out, None, real)
    print(f"wrote {jsonl} and {md}")
    return 0


def cmd_sweep_site(args: argparse.Namespace) -> int:
    from fgen.gen import site_sweep as ss
    from fgen.terrain.model import Terrain, area_tiles

    gd = _gd(args)
    rows = []
    if args.measure:
        from fgen.sim import site_measure

        with _server(args) as srv:
            row = site_measure.flat_row(srv, gd)
        rows.append(row)
        print(f"flat: {row['judged']}", file=sys.stderr)
    else:
        site = ss.flat_site(gd, Terrain(buildable=area_tiles(ss.FLAT_AREA)))
        row, _ = ss.plan_row(ss.FLAT, site, gd, note=_site_note())
        rows.append(row)
    if args.real_map:
        import json as _json

        from fgen.sim import mining, site_measure
        from fgen.sim.server import REPO_CONFIG, HeadlessServer

        seed = _json.loads((REPO_CONFIG / mining.ORE_MAP).read_text()).get("seed")
        with HeadlessServer(map_gen_settings=mining.ORE_MAP) as srv:
            row = site_measure.real_map_row(srv, gd, seed=seed)
        rows.append(row)
        print(f"real map: {row['judged']}", file=sys.stderr)
    jsonl, md = ss.write(Path(args.out_dir), rows, gd)
    print(f"wrote {jsonl} and {md}")
    return 0


def _site_note() -> str:
    from fgen.sim.site_measure import FLAT_NOTE

    return FLAT_NOTE


def cmd_sweep_direct_chain(args: argparse.Namespace, fluid: bool = False) -> int:
    from fgen.gen import direct_chain_sweep as dcs
    from fgen.gen import fluid_sweep

    mod: Any = fluid_sweep if fluid else dcs

    gd = _gd(args)
    out = Path(args.out_dir)
    if not args.measure:
        jsonl, md = mod.write(gd, out)
        print(f"wrote {jsonl} and {md}")
        return 0
    from fgen.sim.measure import RconCharacterizer

    with _server(args) as srv:
        ch = RconCharacterizer(srv, gd)

        def measure(unit):
            m = ch.measure(
                unit,
                warmup_s=dcs.warmup_s(unit, gd, args.warmup),
                sample_s=args.sample_s,
                samples=args.samples,
                speed=args.speed,
            )
            print(
                f"measured {unit.meta['params']['layout']} target "
                f"{unit.meta['target_rate']:g}/s: {m['throughput']:g}/s",
                file=sys.stderr,
            )
            return m

        try:
            jsonl, md = mod.write(gd, out, measure)
        finally:
            ch.clear_all()
    print(f"wrote {jsonl} and {md}")
    return 0


def cmd_sweep(args: argparse.Namespace) -> int:
    if args.family == "mining":
        return cmd_sweep_mining(args)
    if args.family == "direct-chain":
        return cmd_sweep_direct_chain(args)
    if args.family == "fluid":
        return cmd_sweep_direct_chain(args, fluid=True)
    if args.family == "site":
        return cmd_sweep_site(args)
    gd = _gd(args)
    if not args.measure:
        jsonl, md = write_characterization(gd, Path(args.out_dir))
        print(f"wrote {jsonl} and {md}")
        return 0
    from fgen.sim.measure import RconCharacterizer

    with _server(args) as srv:
        ch = RconCharacterizer(srv, gd)

        def measure(unit):
            m = ch.measure(
                unit,
                warmup_s=args.warmup,
                sample_s=args.sample_s,
                samples=args.samples,
                speed=args.speed,
            )
            print(
                f"measured {unit.meta['machine']} x{m['machines']} "
                f"target {unit.meta['target_rate']:g}/s: {m['throughput']:g}/s",
                file=sys.stderr,
            )
            return m

        try:
            jsonl, md = write_characterization(gd, Path(args.out_dir), measure)
        finally:
            ch.clear_all()
    print(f"wrote {jsonl} and {md}")
    return 0


def _server(args: argparse.Namespace):  # -> contextmanager yielding a HeadlessServer
    from contextlib import nullcontext

    from fgen.sim.server import HeadlessServer

    if getattr(args, "attach", None):
        return nullcontext(HeadlessServer.attach(int(args.attach), args.password))
    return HeadlessServer()


def cmd_verify(args: argparse.Namespace) -> int:
    from fgen.sim import verify

    ids = [int(x) for x in args.ids.split(",")] if args.ids else None
    gd = _gd(args)
    with _server(args) as srv:
        results = verify.run(srv, gd, ids, speed=args.speed)
    for r in results:
        print(json.dumps(r, sort_keys=True, default=str))
    for r in results:
        mark = {True: "holds", False: "DIFFERS", None: "n/a"}[r["holds"]]
        print(f"[{r['id']}] {mark}: {r['item']}: {r['conclusion']}", file=sys.stderr)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fgen", description="Factorio blueprint generator")
    parser.add_argument("--data", help="data-raw-dump.json to use instead of the seed data")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("decode", help="blueprint string (or file) -> pretty JSON")
    p.add_argument("blueprint")
    p.set_defaults(fn=cmd_decode)

    p = sub.add_parser("encode", help="JSON file -> blueprint string")
    p.add_argument("json_file")
    p.set_defaults(fn=cmd_encode)

    p = sub.add_parser("render", help="blueprint string (or file) -> ASCII")
    p.add_argument("blueprint")
    p.add_argument("--rulers", action="store_true")
    p.set_defaults(fn=cmd_render)

    p = sub.add_parser("validate", help="blueprint string (or file) -> violations")
    p.add_argument("blueprint")
    p.set_defaults(fn=cmd_validate)

    d = SmeltingRowParams()
    p = sub.add_parser("gen", help="run a generator (mining: on a painted case, see --case)")
    p.add_argument("family", choices=["smelting", "mining", "direct-chain"])
    p.add_argument("--rate", type=float, default=None, help=f"default {d.target_rate:g}")
    p.add_argument("--recipe", default=None, help=f"default {d.recipe} (direct-chain: circuit)")
    p.add_argument("--furnace", default=d.furnace)
    p.add_argument("--belt", default=None, help=f"default {d.belt}")
    p.add_argument("--inserter", default=None, help=f"default {d.inserter}")
    p.add_argument("--machine", default="assembling-machine-1", help="direct-chain")
    p.add_argument(
        "--layout", choices=["solver", "baseline"], default="solver", help="direct-chain"
    )
    p.add_argument("--time-limit", type=float, default=60.0, help="direct-chain: solver seconds")
    p.add_argument(
        "--backend",
        choices=["auto", "exhaustive", "cpsat"],
        default=None,
        help="direct-chain: period solver (auto = cpsat if OR-Tools is installed)",
    )
    p.add_argument(
        "--extra-inserters", default=None, help="direct-chain: comma list of tiers to mix in"
    )
    p.add_argument("--pole", default=None, help=f"default {d.pole}")
    p.add_argument("--mirror", action="store_true", help="direct-chain: mirror the machines")
    p.add_argument("--case", default="rect-2belt", help="mining: painted case name")
    p.add_argument("--resource", default=None, help="mining")
    p.add_argument("--drill", default=None, help="mining")
    p.add_argument("--axis", choices=["x", "y"], default=None, help="mining")
    p.add_argument("--flow", choices=["+", "-"], default=None, help="mining")
    p.add_argument("--max-belts", type=int, default=None, help="mining")
    p.add_argument("--min-ore", type=int, default=None, help="mining: min_ore_tiles_per_drill")
    p.add_argument("--sides", type=int, default=d.sides)
    p.add_argument("--fuel", default=d.fuel)
    p.add_argument("--no-fuel-lane", action="store_true")
    p.add_argument("--json", action="store_true", help="print predict() as JSON")
    p.set_defaults(fn=cmd_gen)

    p = sub.add_parser("sweep", help="parameter sweep -> characterization/")
    p.add_argument("family", choices=["smelting", "mining", "site", "direct-chain", "fluid"])
    p.add_argument(
        "--real-map", action="store_true", help="mining/site: end-to-end run on the ore map"
    )
    p.add_argument("--out-dir", default="characterization")
    p.add_argument("--measure", action="store_true", help="measure each unit in headless")
    p.add_argument("--warmup", type=float, default=60.0, help="warm-up, game seconds")
    p.add_argument("--sample-s", type=float, default=10.0, help="sample period, game seconds")
    p.add_argument("--samples", type=int, default=6)
    p.add_argument("--speed", type=float, default=64.0, help="game.speed while measuring")
    p.add_argument("--attach", metavar="PORT", help="use a running server's RCON port")
    p.add_argument("--password", default="fgen", help="RCON password with --attach")
    p.set_defaults(fn=cmd_sweep)

    p = sub.add_parser("verify", help="run the in-game VERIFY.md checks (needs headless)")
    p.add_argument("--ids", help="comma separated check ids (default: all)")
    p.add_argument("--speed", type=float, default=10.0, help="game.speed while checking")
    p.add_argument("--attach", metavar="PORT", help="use a running server's RCON port")
    p.add_argument("--password", default="fgen", help="RCON password with --attach")
    p.set_defaults(fn=cmd_verify)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except (ValueError, KeyError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
