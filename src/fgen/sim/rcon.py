"""Source RCON client (standard library only).

Packet: ``int32 size | int32 id | int32 type | body \\0 | \\0`` (little endian, ``size``
counts everything after itself). Auth with ``SERVERDATA_AUTH`` (3); the server answers
with an ``SERVERDATA_AUTH_RESPONSE`` (2) whose id is ``-1`` on a bad password. Commands
are ``SERVERDATA_EXECCOMMAND`` (2), answers ``SERVERDATA_RESPONSE_VALUE`` (0).

Factorio 2.0.76 behaviour (measured):

- Every command gets **exactly one** response packet with the request's id, even when
  it prints nothing (empty body) and even when the body is far larger than 4096 bytes
  (a 60 kB ``rcon.print`` arrives as one packet). The usual "send an empty
  RESPONSE_VALUE as end marker" trick does not work: Factorio never echoes it. So a
  command is complete when the packet with its id arrives.
- Requests of at least 200 kB are accepted (blueprint strings fit easily).
- ``/sc <lua>`` runs silently; output comes only from ``rcon.print``. A Lua error is
  reported as body ``Cannot execute command. Error: ...``, after anything printed
  before the error.
- ``helpers.table_to_json`` (2.0; ``game.table_to_json`` is gone) serialises results.
"""

from __future__ import annotations

import json
import socket
import struct
import threading
from typing import Any

AUTH = 3
AUTH_RESPONSE = 2
EXECCOMMAND = 2
RESPONSE_VALUE = 0


class RconError(RuntimeError):
    pass


class LuaError(RconError):
    """A ``/sc`` command raised a Lua error; ``output`` holds the whole response."""

    def __init__(self, message: str, output: str) -> None:
        super().__init__(message)
        self.output = output


LUA_ERROR_MARKER = "Cannot execute command. Error:"


class RconClient:
    def __init__(self, host: str, port: int, password: str, timeout: float = 30.0) -> None:
        self.host, self.port, self.password, self.timeout = host, port, password, timeout
        self._sock: socket.socket | None = None
        self._next_id = 1
        self._lock = threading.Lock()

    def connect(self) -> None:
        s = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self._sock = s
        rid = self._send(AUTH, self.password)
        while True:
            pid, ptype, _ = self._recv()
            if ptype == AUTH_RESPONSE:
                if pid == -1 or pid != rid:
                    self.close()
                    raise RconError("RCON authentication failed")
                return

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None

    def __enter__(self) -> RconClient:
        if self._sock is None:
            self.connect()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- wire ---------------------------------------------------------------
    def _send(self, ptype: int, body: str) -> int:
        assert self._sock is not None, "not connected"
        rid = self._next_id
        self._next_id = self._next_id % 0x7FFFFFF0 + 1
        data = body.encode("utf-8")
        pkt = struct.pack("<ii", rid, ptype) + data + b"\0\0"
        self._sock.sendall(struct.pack("<i", len(pkt)) + pkt)
        return rid

    def _read_exact(self, n: int) -> bytes:
        assert self._sock is not None
        buf = bytearray()
        while len(buf) < n:
            chunk = self._sock.recv(n - len(buf))
            if not chunk:
                raise RconError("RCON connection closed")
            buf += chunk
        return bytes(buf)

    def _recv(self) -> tuple[int, int, bytes]:
        (size,) = struct.unpack("<i", self._read_exact(4))
        payload = self._read_exact(size)
        pid, ptype = struct.unpack("<ii", payload[:8])
        return pid, ptype, payload[8:-2]

    # -- API ----------------------------------------------------------------
    def command(self, cmd: str) -> str:
        with self._lock:
            if self._sock is None:
                self.connect()
            rid = self._send(EXECCOMMAND, cmd)
            parts: list[bytes] = []
            while True:
                pid, _ptype, body = self._recv()
                if pid == rid:
                    parts.append(body)
                    return b"".join(parts).decode("utf-8", errors="replace")

    def lua(self, code: str) -> str:
        """Run ``code`` with ``/sc``; returns what it ``rcon.print``-ed. Raises
        :class:`LuaError` on a Lua error."""
        out = self.command("/sc " + code)
        i = out.find(LUA_ERROR_MARKER)
        if i >= 0:
            raise LuaError(out[i + len(LUA_ERROR_MARKER) :].strip(), out)
        return out

    def lua_json(self, code: str) -> Any:
        """Run ``code`` and parse the **last** printed line as JSON."""
        out = self.lua(code).strip()
        if not out:
            raise RconError("Lua printed nothing; expected JSON")
        return json.loads(out.splitlines()[-1])
