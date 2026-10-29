"""Keeping the run save after stop (no server: the save written by ``/quit`` is faked)."""

from __future__ import annotations

from pathlib import Path

from fgen.sim.server import HeadlessServer


def _server_with_run_save(tmp_path: Path, **kw) -> HeadlessServer:
    srv = HeadlessServer(workdir=tmp_path / "work", **kw)
    srv.run_save.parent.mkdir(parents=True)
    srv.run_save.write_bytes(b"PK-fake-save")
    return srv


def test_no_env_no_label_keeps_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("FGEN_SAVE_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    srv = _server_with_run_save(tmp_path)
    srv._keep_save()
    assert srv.kept_save is None
    assert not (tmp_path / "saves").exists()


def test_label_keeps_under_saves_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("FGEN_SAVE_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    srv = _server_with_run_save(tmp_path, save_label="green-circuit-2ps")
    srv._keep_save()
    assert srv.kept_save == tmp_path / "saves" / "green-circuit-2ps.zip"
    assert srv.kept_save.read_bytes() == b"PK-fake-save"


def test_env_dir_and_default_label(tmp_path, monkeypatch):
    monkeypatch.setenv("FGEN_SAVE_DIR", str(tmp_path / "out"))
    srv = _server_with_run_save(tmp_path, map_gen_settings="ore-map-gen-settings.json")
    srv._keep_save()
    assert srv.kept_save is not None
    assert srv.kept_save.parent == tmp_path / "out"
    assert srv.kept_save.name.startswith("ore-") and srv.kept_save.suffix == ".zip"


def test_missing_run_save_is_fine(tmp_path, monkeypatch):
    monkeypatch.setenv("FGEN_SAVE_DIR", str(tmp_path / "out"))
    srv = HeadlessServer(workdir=tmp_path / "work", save_label="x")
    srv._keep_save()
    assert srv.kept_save is None
