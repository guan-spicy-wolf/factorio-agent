import json
from pathlib import Path

import pytest

from fgen.blueprint import codec
from fgen.cli import main

FIXTURES = Path(__file__).parent / "fixtures"


def test_gen_smelting(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["gen", "smelting", "--rate", "7.5"]) == 0
    out = capsys.readouterr().out
    assert "interfaces:" in out and "predict:" in out
    bp = codec.decode(out.strip().splitlines()[-1])
    assert bp["blueprint"]["entities"]


def test_gen_infeasible_exit_code(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["gen", "smelting", "--rate", "30"]) == 2
    assert "try:" in capsys.readouterr().err


def test_decode_encode_render_validate(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["gen", "smelting", "--rate", "5", "--pole", "medium-electric-pole"]) == 0
    s = capsys.readouterr().out.strip().splitlines()[-1]
    bpfile = tmp_path / "bp.txt"
    bpfile.write_text(s)
    assert main(["decode", str(bpfile)]) == 0
    obj = json.loads(capsys.readouterr().out)
    jf = tmp_path / "bp.json"
    jf.write_text(json.dumps(obj))
    assert main(["encode", str(jf)]) == 0
    assert codec.decode(capsys.readouterr().out.strip()) == obj
    assert main(["render", s]) == 0
    assert "F" in capsys.readouterr().out
    # a bare blueprint has no interface declarations, so belt ends are reported
    assert main(["validate", s]) == 1
    out = capsys.readouterr().out
    assert "belt.discontinuous" in out and "overlap" not in out


def test_validate_real_fixture(capsys: pytest.CaptureFixture[str]) -> None:
    path = FIXTURES / "bp_2.0.55_assembler.txt"
    if not path.exists():
        pytest.skip("fixture missing")
    assert main(["validate", str(path)]) == 1  # unpowered assembler
    assert "power.uncovered" in capsys.readouterr().out


def test_sweep(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["sweep", "smelting", "--out-dir", str(tmp_path)]) == 0
    rows = [json.loads(line) for line in (tmp_path / "smelting_row.jsonl").read_text().splitlines()]
    assert len(rows) == 3 * 2 * 3
    assert all(r["measured"] is None for r in rows)
    assert any(r["predicted"] for r in rows)
    md = (tmp_path / "smelting_row.md").read_text()
    for rate in ["5", "7.5", "15"]:
        assert f"## target_rate = {rate}/s" in md


def test_bad_input(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["decode", "not-a-blueprint"]) == 1
    assert "error" in capsys.readouterr().err
