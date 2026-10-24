"""Server-free tests of the sim package: RCON framing against a fake server, Lua
literals, and that the protocol defaults stay NotAvailable."""

from __future__ import annotations

import socket
import struct
import threading

import pytest

from fgen.sim import lua
from fgen.sim.rcon import AUTH, AUTH_RESPONSE, RESPONSE_VALUE, LuaError, RconClient, RconError


def _fake_server(password: str, replies: dict[str, str]):
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def recv_exact(c, n):
        b = b""
        while len(b) < n:
            chunk = c.recv(n - len(b))
            if not chunk:
                raise ConnectionError
            b += chunk
        return b

    def send(c, rid, t, body):
        pkt = struct.pack("<ii", rid, t) + body.encode() + b"\0\0"
        c.sendall(struct.pack("<i", len(pkt)) + pkt)

    def run():
        c, _ = srv.accept()
        try:
            while True:
                (size,) = struct.unpack("<i", recv_exact(c, 4))
                data = recv_exact(c, size)
                rid, t = struct.unpack("<ii", data[:8])
                body = data[8:-2].decode()
                if t == AUTH:
                    send(c, RESPONSE_VALUE, RESPONSE_VALUE, "")  # like Source: empty first
                    send(c, rid if body == password else -1, AUTH_RESPONSE, "")
                else:
                    send(c, rid + 1000, RESPONSE_VALUE, "stale")  # ignored: other id
                    send(c, rid, RESPONSE_VALUE, replies.get(body, ""))
        except (ConnectionError, OSError):
            pass
        finally:
            c.close()
            srv.close()

    threading.Thread(target=run, daemon=True).start()
    return port


def test_rcon_roundtrip_big_body_and_errors():
    big = "x" * 70000
    port = _fake_server(
        "pw",
        {
            "/sc a": "1\n",
            "/sc big": big,
            "/sc bad": "partial\nCannot execute command. Error: boom\n",
            "/sc j": 'noise\n{"a": [1, 2]}\n',
        },
    )
    with RconClient("127.0.0.1", port, "pw", timeout=5) as c:
        assert c.lua("a") == "1\n"
        assert c.lua("big") == big
        with pytest.raises(LuaError, match="boom") as ei:
            c.lua("bad")
        assert ei.value.output.startswith("partial")
        assert c.lua_json("j") == {"a": [1, 2]}


def test_rcon_bad_password():
    port = _fake_server("pw", {})
    with pytest.raises(RconError, match="authentication"):
        RconClient("127.0.0.1", port, "nope", timeout=5).connect()


def test_lua_literals():
    assert lua.lit(None) == "nil"
    assert lua.lit(True) == "true"
    assert lua.lit(3) == "3"
    assert lua.lit(0.5) == "0.5"
    assert lua.lit('a"b') == '"a\\"b"'
    assert lua.lit([1, "x"]) == '{1, "x"}'
    assert lua.lit({"k": 1}) == '{["k"] = 1}'
    assert lua.area_lit(((0, 1), (2, 3))) == "{{0.0, 1.0}, {2.0, 3.0}}"


def test_protocol_defaults_still_not_available():
    from fgen.sim.protocol import NotAvailableDeployer

    with pytest.raises(RuntimeError, match="headless"):
        NotAvailableDeployer().place(None, None)  # type: ignore[arg-type]
