"""Command line entry point: ``uv run fgen <cmd>`` (plan §4.8)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

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
    return SmeltingRowParams(
        recipe=args.recipe,
        furnace=args.furnace,
        target_rate=args.rate,
        belt=args.belt,
        inserter=args.inserter,
        pole=args.pole,
        sides=args.sides,
        fuel_lane=not args.no_fuel_lane,
        fuel=args.fuel,
    )


def cmd_gen(args: argparse.Namespace) -> int:
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


def cmd_sweep(args: argparse.Namespace) -> int:
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
    p = sub.add_parser("gen", help="run a generator")
    p.add_argument("family", choices=["smelting"])
    p.add_argument("--rate", type=float, default=d.target_rate)
    p.add_argument("--recipe", default=d.recipe)
    p.add_argument("--furnace", default=d.furnace)
    p.add_argument("--belt", default=d.belt)
    p.add_argument("--inserter", default=d.inserter)
    p.add_argument("--pole", default=d.pole)
    p.add_argument("--sides", type=int, default=d.sides)
    p.add_argument("--fuel", default=d.fuel)
    p.add_argument("--no-fuel-lane", action="store_true")
    p.add_argument("--json", action="store_true", help="print predict() as JSON")
    p.set_defaults(fn=cmd_gen)

    p = sub.add_parser("sweep", help="parameter sweep -> characterization/")
    p.add_argument("family", choices=["smelting"])
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
