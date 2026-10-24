"""Factorio headless server for tests and measurements.

Facts established against 2.0.76 and re-checked on 2.1.23 (headless, Linux):

- The binary is ``$FACTORIO_DIR/bin/x64/factorio``. We run it with ``-c <workdir>/config.ini``
  whose ``write-data`` points into the work directory (so logs, saves and
  ``player-data.json`` never touch the install) and ``--mod-directory <workdir>/mods``
  with a ``mod-list.json`` that enables exactly ``mods`` (default: base + space-age +
  quality + elevated-rails, the project's target set): the mod set changes prototypes
  (e.g. recipe categories), so it must be explicit and match the seed data.
- ``--create <save> --map-gen-settings F --map-settings F`` writes a new map in ~4 s and
  exits. ``water`` ``size`` 0 alone still leaves lakes; pinning
  ``property_expression_names.elevation`` to a constant removes them. Rocks are their
  own autoplace control (``rocks``) in 2.0.
- ``--start-server <save> --rcon-bind 127.0.0.1:<port> --rcon-password <pw>
  --server-settings F --port <udp>`` hosts the game. RCON is accepting connections once
  the log prints ``Starting RCON interface at IP ADDR:({127.0.0.1:<port>})``.
- With ``auto_pause: false`` the map keeps ticking with nobody connected.
- The **first** ``/sc`` after a map is loaded returns an empty response and runs
  nothing visible (the "commands disable achievements" confirmation); it has to be
  repeated. :meth:`HeadlessServer.start` primes this.
- ``/quit`` over RCON saves the map into the save file and exits cleanly (~0.3 s), so
  each start copies a pristine template save to a run save first.
"""

from __future__ import annotations

import atexit
import contextlib
import ctypes
import os
import shutil
import signal
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Self

from fgen.sim.rcon import RconClient, RconError

REPO_CONFIG = Path(__file__).resolve().parents[3] / "config"
READY_MARKER = "Starting RCON interface"
DEFAULT_MODS = ("base", "space-age", "quality", "elevated-rails", "recycler")
ALL_STOCK_MODS = ("base", "elevated-rails", "quality", "space-age", "recycler")  # 2.1 adds recycler


def factorio_dir() -> Path:
    return Path(os.environ.get("FACTORIO_DIR", Path.home() / "factorio")).expanduser()


def factorio_binary(fdir: Path | None = None) -> Path:
    return (fdir or factorio_dir()) / "bin" / "x64" / "factorio"


def binary_available(fdir: Path | None = None) -> bool:
    b = factorio_binary(fdir)
    return b.is_file() and os.access(b, os.X_OK)


def free_port(kind: int = socket.SOCK_STREAM) -> int:
    with socket.socket(socket.AF_INET, kind) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _die_with_parent() -> None:  # pragma: no cover - runs in the child
    """Ask Linux to SIGKILL the server if the Python process dies (no orphans)."""
    with contextlib.suppress(Exception):
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        pr_set_pdeathsig = 1
        libc.prctl(pr_set_pdeathsig, signal.SIGKILL)


class ServerError(RuntimeError):
    pass


class HeadlessServer:
    """Context manager: create the flat test map if needed, start, wait for RCON, stop.

    ``map_gen_settings`` names a file in ``config/`` (default: the flat field;
    ``ore-map-gen-settings.json`` is a normal map with resources, see plan-phase2 §3.5).

    ``with HeadlessServer() as srv: srv.rcon.lua("rcon.print(game.tick)")``
    """

    def __init__(
        self,
        factorio_dir_: Path | None = None,
        workdir: Path | None = None,
        port: int | None = None,
        password: str = "fgen",
        *,
        config_dir: Path = REPO_CONFIG,
        mods: tuple[str, ...] = DEFAULT_MODS,
        startup_timeout: float = 60.0,
        map_gen_settings: str = "test-map-gen-settings.json",
    ) -> None:
        self.factorio_dir = Path(factorio_dir_ or factorio_dir())
        self.binary = factorio_binary(self.factorio_dir)
        self._own_workdir = workdir is None
        self.workdir = Path(workdir or tempfile.mkdtemp(prefix="fgen-headless-"))
        self.port = port
        self.password = password
        self.config_dir = Path(config_dir)
        self.mods = mods
        self.startup_timeout = startup_timeout
        self.proc: subprocess.Popen[bytes] | None = None
        self.rcon: RconClient = None  # type: ignore[assignment]
        self.log_path = self.workdir / "server.log"
        self.map_gen_settings = map_gen_settings
        stem = Path(map_gen_settings).stem.removesuffix("-map-gen-settings")
        name = "flat" if stem == "test" else stem
        self.template_save = self.workdir / f"{name}-template.zip"
        self.run_save = self.workdir / "run.zip"

    # -- files ---------------------------------------------------------------
    def _prepare_workdir(self) -> None:
        self.workdir.mkdir(parents=True, exist_ok=True)
        (self.workdir / "config.ini").write_text(
            f"[path]\nread-data=__PATH__executable__/../../data\nwrite-data={self.workdir}\n"
        )
        mods_dir = self.workdir / "mods"
        mods_dir.mkdir(exist_ok=True)
        names = list(dict.fromkeys([*ALL_STOCK_MODS, *self.mods]))
        entries = ",".join(
            f'{{"name": "{n}", "enabled": {"true" if n in self.mods else "false"}}}' for n in names
        )
        (mods_dir / "mod-list.json").write_text(f'{{"mods": [{entries}]}}\n')

    def _base_args(self) -> list[str]:
        return [
            str(self.binary),
            "-c",
            str(self.workdir / "config.ini"),
            "--mod-directory",
            str(self.workdir / "mods"),
        ]

    def create_save(self) -> Path:
        if self.template_save.exists():
            return self.template_save
        out = subprocess.run(
            [
                *self._base_args(),
                "--create",
                str(self.template_save),
                "--map-gen-settings",
                str(self.config_dir / self.map_gen_settings),
                "--map-settings",
                str(self.config_dir / "test-map-settings.json"),
            ],
            capture_output=True,
            timeout=300,
            check=False,
        )
        if out.returncode != 0 or not self.template_save.exists():
            tail = out.stdout.decode(errors="replace")[-2000:]
            raise ServerError(f"--create failed ({out.returncode}):\n{tail}")
        return self.template_save

    # -- lifecycle -----------------------------------------------------------
    def start(self) -> Self:
        if not binary_available(self.factorio_dir):
            raise ServerError(f"factorio binary not found at {self.binary}")
        if self.proc is not None:
            raise ServerError("server already started")
        self._prepare_workdir()
        self.create_save()
        shutil.copyfile(self.template_save, self.run_save)
        if self.port is None:
            self.port = free_port()
        udp = free_port(socket.SOCK_DGRAM)
        log = self.log_path.open("wb")
        self.proc = subprocess.Popen(
            [
                *self._base_args(),
                "--start-server",
                str(self.run_save),
                "--rcon-bind",
                f"127.0.0.1:{self.port}",
                "--rcon-password",
                self.password,
                "--server-settings",
                str(self.config_dir / "test-server-settings.json"),
                "--port",
                str(udp),
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            preexec_fn=_die_with_parent,
        )
        log.close()
        atexit.register(self.stop)
        try:
            self._wait_ready()
            self.rcon = RconClient("127.0.0.1", self.port, self.password)
            self.rcon.connect()
            self._prime()
        except BaseException:
            self.stop()
            raise
        return self

    def _wait_ready(self) -> None:
        deadline = time.monotonic() + self.startup_timeout
        while time.monotonic() < deadline:
            if self.proc is not None and self.proc.poll() is not None:
                raise ServerError(
                    f"server exited early ({self.proc.returncode}):\n{self.log_tail()}"
                )
            if READY_MARKER in self.log_path.read_text(errors="replace"):
                return
            time.sleep(0.05)
        raise ServerError(f"server not ready after {self.startup_timeout}s:\n{self.log_tail()}")

    def _prime(self) -> None:
        for _ in range(5):
            if self.rcon.lua('rcon.print("fgen-ready")').strip() == "fgen-ready":
                return
        raise ServerError("/sc never answered")

    def log_tail(self, n: int = 3000) -> str:
        try:
            return self.log_path.read_text(errors="replace")[-n:]
        except OSError:
            return ""

    def stop(self, timeout: float = 15.0) -> None:
        proc, self.proc = self.proc, None
        if proc is None:
            return
        with contextlib.suppress(Exception):
            atexit.unregister(self.stop)
        if proc.poll() is None and self.rcon is not None:
            with contextlib.suppress(OSError, RconError):
                self.rcon.command("/quit")
        if self.rcon is not None:
            self.rcon.close()
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        if self._own_workdir:
            shutil.rmtree(self.workdir, ignore_errors=True)

    @classmethod
    def attach(cls, port: int, password: str, host: str = "127.0.0.1") -> HeadlessServer:
        """Wrap an already running server (not owned: :meth:`stop` leaves it alone)."""
        srv = cls(workdir=Path(tempfile.gettempdir()), port=port, password=password)
        srv._own_workdir = False
        srv._attached = True
        srv.rcon = RconClient(host, port, password)
        srv.rcon.connect()
        srv._prime()
        return srv

    _attached = False

    @property
    def running(self) -> bool:
        if self._attached:
            return True
        return self.proc is not None and self.proc.poll() is None

    @property
    def pid(self) -> int | None:
        return None if self.proc is None else self.proc.pid

    def __enter__(self) -> Self:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()

    # -- game helpers ----------------------------------------------------------
    def tick(self) -> int:
        return int(self.rcon.lua("rcon.print(game.tick)"))

    def set_speed(self, x: float) -> None:
        self.rcon.lua(f"game.speed = {float(x)!r}")

    def wait_ticks(self, n: int, poll: float = 0.02, timeout: float | None = None) -> int:
        """Block until ``game.tick`` has advanced by ``n`` (game time, not wall time)."""
        return self.wait_until_tick(self.tick() + n, poll=poll, timeout=timeout)

    def wait_until_tick(self, target: int, poll: float = 0.02, timeout: float | None = None) -> int:
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            t = self.tick()
            if t >= target:
                return t
            if deadline is not None and time.monotonic() > deadline:
                raise ServerError(f"tick {t} did not reach {target} in time")
            if not self.running:
                raise ServerError("server died while waiting for ticks")
            time.sleep(poll)
