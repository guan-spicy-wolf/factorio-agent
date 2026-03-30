"""Factorio headless server lifecycle management."""

import subprocess
import time
import signal
import os
from pathlib import Path

from agent.rcon import RCONClient, ConnectionError


class ServerError(Exception):
    """Factorio server error."""


class FactorioServer:
    """Manages a Factorio headless server process."""

    def __init__(
        self,
        factorio_path: str | Path = "./factorio-server",
        save_path: str | Path = "./saves/agent-dev.zip",
        server_settings: str | Path = "./config/server-settings.json",
        map_gen_settings: str | Path = "./config/map-gen-settings.json",
        rcon_host: str = "127.0.0.1",
        rcon_port: int = 27015,
        rcon_password: str = "changeme",
    ):
        self.factorio_path = Path(factorio_path)
        self.save_path = Path(save_path)
        self.server_settings = Path(server_settings)
        self.map_gen_settings = Path(map_gen_settings)
        self.rcon_host = rcon_host
        self.rcon_port = rcon_port
        self.rcon_password = rcon_password
        self._process: subprocess.Popen | None = None

    @property
    def binary(self) -> Path:
        return self.factorio_path / "bin" / "x64" / "factorio"

    @property
    def mods_dir(self) -> Path:
        return self.factorio_path / "mods"

    def ensure_mod_linked(self, mod_path: str | Path = "./mod") -> None:
        """Symlink the agent mod into the server's mods directory."""
        mod_path = Path(mod_path).resolve()
        link_path = self.mods_dir / "factorio-agent"
        self.mods_dir.mkdir(parents=True, exist_ok=True)
        if link_path.is_symlink() or link_path.exists():
            link_path.unlink()
        link_path.symlink_to(mod_path)

    def create_save(self) -> None:
        """Create a new save file with map generation settings."""
        self.save_path.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [
                str(self.binary),
                "--create", str(self.save_path),
                "--map-gen-settings", str(self.map_gen_settings),
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise ServerError(f"Failed to create save: {result.stderr}")

    def start(self, timeout: float = 30.0) -> None:
        """Start the server and wait for RCON to become available."""
        if self._process and self._process.poll() is None:
            raise ServerError("Server already running")

        if not self.binary.exists():
            raise ServerError(f"Factorio binary not found: {self.binary}")

        if not self.save_path.exists():
            raise ServerError(f"Save file not found: {self.save_path}")

        self._process = subprocess.Popen(
            [
                str(self.binary),
                "--start-server", str(self.save_path),
                "--server-settings", str(self.server_settings),
                "--rcon-port", str(self.rcon_port),
                "--rcon-password", self.rcon_password,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

        # Wait for RCON readiness
        self._wait_for_rcon(timeout)

    def _wait_for_rcon(self, timeout: float) -> None:
        """Poll RCON port until the server is ready."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                client = RCONClient(self.rcon_host, self.rcon_port,
                                    self.rcon_password, timeout=2.0)
                client.connect()
                client.close()
                return
            except (ConnectionError, OSError):
                time.sleep(0.5)

            # Check if process died
            if self._process and self._process.poll() is not None:
                raise ServerError(
                    f"Server exited with code {self._process.returncode}"
                )

        raise ServerError(f"RCON not available after {timeout}s")

    def stop(self, timeout: float = 15.0) -> None:
        """Stop the server gracefully."""
        if not self._process or self._process.poll() is not None:
            self._process = None
            return

        self._process.send_signal(signal.SIGTERM)
        try:
            self._process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=5)
        self._process = None

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def __enter__(self) -> "FactorioServer":
        self.start()
        return self

    def __exit__(self, *args) -> None:
        self.stop()
