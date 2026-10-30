"""Phase 9 task B scaffold (fgen.compare): variant files, the side-by-side runner, the table."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fgen.cli import main
from fgen.compare import runner, table
from fgen.compare.variants import VariantSet, families
from fgen.gen.direct_chain import DirectChainParams
from fgen.gen.smelting_row import SmeltingRowParams

ROOT = Path(__file__).parent.parent
SMOKE = ROOT / "config" / "variants" / "circuit-smoke.json"


def _write(tmp_path: Path, d: dict) -> Path:
    p = tmp_path / f"{d['name']}.json"
    p.write_text(json.dumps(d))
    return p


def test_variant_file_expands_overrides_and_grid(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        {
            "name": "x",
            "family": "direct-chain",
            "base": {"target_rate": 2.0, "backend": "exhaustive"},
            "variants": [{"name": "a", "params": {"extra_inserters": ["long-handed-inserter"]}}],
            "grid": {"pole": ["small-electric-pole", "medium-electric-pole"], "prove": [True]},
        },
    )
    vs = VariantSet.load(p)
    ms = vs.members()
    assert [m.name for m in ms] == [
        "a",
        "pole=small-electric-pole;prove=True",  # grid values keep the file's order
        "pole=medium-electric-pole;prove=True",
    ]
    assert all(isinstance(m.params, DirectChainParams) for m in ms)
    assert ms[0].params.extra_inserters == ("long-handed-inserter",)  # list -> tuple
    assert ms[0].params.target_rate == 2.0 and ms[0].params.backend == "exhaustive"
    assert ms[2].params.pole == "medium-electric-pole" and ms[2].params.prove is True
    assert vs.to_json()["variants"][0]["params"] == {"extra_inserters": ["long-handed-inserter"]}


@pytest.mark.parametrize(
    ("bad", "msg"),
    [
        ({"family": "mining"}, "family"),
        ({"rank": "best"}, "rank"),
        ({"variants": [{"name": "a", "params": {"palete": "full"}}]}, "unknown parameter"),
        ({"variants": [{"name": "a", "params": {}}, {"name": "a", "params": {}}]}, "duplicate"),
        ({"variants": [], "grid": {}}, "no variants"),
        ({"grid": {"pole": []}}, "empty"),
    ],
)
def test_variant_file_rejects_mistakes(tmp_path: Path, bad: dict, msg: str) -> None:
    d = {"name": "x", "family": "direct-chain", "variants": [{"name": "a", "params": {}}], **bad}
    with pytest.raises(ValueError, match=msg):
        VariantSet.load(_write(tmp_path, d))


def test_shipped_variant_files_load() -> None:
    for p in (ROOT / "config" / "variants").glob("*.json"):
        vs = VariantSet.load(p)
        assert vs.members(), p
        assert vs.family in families()


def test_smoke_rows_are_the_same_in_parallel(gd) -> None:
    vs = VariantSet.load(SMOKE)
    rows1, meta1 = runner.run(gd, vs, procs=1)
    rows2, meta2 = runner.run(gd, vs, procs=2)
    assert meta1["procs"] == 1 and meta2["procs"] == 2
    by1 = {r["variant"]: r for r in rows1}
    by2 = {r["variant"]: r for r in rows2}
    assert set(by1) == set(by2) == {m.name for m in vs.members()}
    for name, r in by1.items():
        q = by2[name]
        assert (r["status"], r.get("blueprint_sha"), r.get("reason")) == (
            q["status"],
            q.get("blueprint_sha"),
            q.get("reason"),
        ), name
        if r["status"] == "ok":
            assert r["period"]["width"] == q["period"]["width"]
            assert r["key"] == q["key"] and r["rank"] == q["rank"]
    # the three kinds of row
    assert by1["solver"]["status"] == "ok" and by1["solver"]["period"]["proven"] is True
    assert by1["baseline"]["status"] == "ok" and "period" in by1["baseline"]
    assert by1["bad-machine"]["status"] == "infeasible"
    assert "stone-furnace" in by1["bad-machine"]["reason"]
    assert by1["bad-machine"]["suggestions"]  # the generator's own concrete fixes
    # the proven 19x7 period is not beaten by anything in the set under `compact`
    best = runner.rank(rows1, "compact")[0]
    assert best["period"]["area"] == by1["solver"]["period"]["area"]
    assert meta1["winner"] == best["variant"]
    # `first` ranks feasible rows by seconds to the first period, never an infeasible one
    assert all(r["status"] == "ok" for r in runner.rank(rows1, "first"))


def test_smelting_family_ranks_by_area(gd, tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        {
            "name": "rows",
            "family": "smelting",
            "base": {"target_rate": 7.5},
            "grid": {"furnace": ["stone-furnace", "steel-furnace"]},
        },
    )
    vs = VariantSet.load(p)
    assert all(isinstance(m.params, SmeltingRowParams) for m in vs.members())
    rows, meta = runner.run(gd, vs, procs=1)
    assert [r["status"] for r in rows] == ["ok", "ok"]
    assert all("period" not in r for r in rows)
    areas = {r["variant"]: r["predicted"]["area"] for r in rows}
    assert meta["winner"] == min(areas, key=lambda k: (areas[k], list(areas).index(k)))
    md = table.render_md(vs, rows, meta, gd)
    assert any(line.startswith("| rank | variant |") for line in md)
    assert "first feasible s" not in "\n".join(md)


def test_cli_compare_writes_the_table(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["compare", str(SMOKE), "--procs", "1", "--out-dir", str(tmp_path)]) == 0
    err = capsys.readouterr().err
    assert "winner by compact:" in err and "bad-machine: infeasible" in err
    rows = [
        json.loads(line) for line in (tmp_path / "circuit-smoke.jsonl").read_text().splitlines()
    ]
    assert len(rows) == 5 and rows[0]["variant"] == "solver"
    md = (tmp_path / "circuit-smoke.md").read_text()
    assert "## Members" in md and "Winner by `compact`" in md and "## Units" in md
    assert "## Suggestions on infeasible members" in md
    # --only and --rank
    assert (
        main(
            [
                "compare",
                str(SMOKE),
                "--procs",
                "1",
                "--only",
                "solver,baseline",
                "--rank",
                "first",
                "--out-dir",
                str(tmp_path / "sub"),
            ]
        )
        == 0
    )
    rows = [
        json.loads(line) for line in (tmp_path / "sub/circuit-smoke.jsonl").read_text().splitlines()
    ]
    assert [r["variant"] for r in rows] == ["solver", "baseline"]
    # nothing feasible -> exit code 2
    assert (
        main(
            [
                "compare",
                str(SMOKE),
                "--procs",
                "1",
                "--only",
                "bad-machine",
                "--out-dir",
                str(tmp_path / "none"),
            ]
        )
        == 2
    )
    assert main(["compare", str(SMOKE), "--only", "nope", "--out-dir", str(tmp_path)]) == 1
