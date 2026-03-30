"""Source RCON protocol client for Factorio headless server."""

import socket
import struct
from typing import Optional


# Packet types (Source RCON protocol)
SERVERDATA_AUTH = 3
SERVERDATA_AUTH_RESPONSE = 2
SERVERDATA_EXECCOMMAND = 2
SERVERDATA_RESPONSE_VALUE = 0


class RCONError(Exception):
    """Base exception for RCON errors."""


class AuthenticationError(RCONError):
    """RCON authentication failed."""


class ConnectionError(RCONError):
    """RCON connection failed."""


def _pack_packet(request_id: int, packet_type: int, body: str) -> bytes:
    """Encode an RCON packet.

    Format: [4B size][4B request_id][4B type][body + \\x00][\\x00]
    Size field = len(request_id + type + body + two null terminators) = 4+4+len(body)+2
    """
    body_bytes = body.encode("utf-8")
    size = 4 + 4 + len(body_bytes) + 2
    return struct.pack(f"<iii{len(body_bytes)}scc", size, request_id, packet_type,
                       body_bytes, b"\x00", b"\x00")


def _unpack_packet(data: bytes) -> tuple[int, int, str]:
    """Decode an RCON packet. Returns (request_id, packet_type, body)."""
    if len(data) < 12:
        raise RCONError(f"Packet too short: {len(data)} bytes")
    request_id, packet_type = struct.unpack_from("<ii", data, 0)
    # Body is everything after the two ints, minus the two null terminators
    body = data[8:-2].decode("utf-8", errors="replace")
    return request_id, packet_type, body


class RCONClient:
    """Client for the Source RCON protocol used by Factorio."""

    def __init__(self, host: str = "127.0.0.1", port: int = 27015,
                 password: str = "", timeout: float = 10.0):
        self.host = host
        self.port = port
        self.password = password
        self.timeout = timeout
        self._socket: Optional[socket.socket] = None
        self._request_id = 0

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def connect(self) -> None:
        """Connect to the RCON server and authenticate."""
        try:
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._socket.settimeout(self.timeout)
            self._socket.connect((self.host, self.port))
        except OSError as e:
            raise ConnectionError(f"Failed to connect to {self.host}:{self.port}: {e}") from e

        self._authenticate()

    def _authenticate(self) -> None:
        """Send auth packet and verify response."""
        auth_id = self._next_id()
        self._send(auth_id, SERVERDATA_AUTH, self.password)
        resp_id, resp_type, _ = self._recv()

        # Factorio may send an empty RESPONSE_VALUE before the AUTH_RESPONSE
        if resp_type == SERVERDATA_RESPONSE_VALUE:
            resp_id, resp_type, _ = self._recv()

        if resp_type != SERVERDATA_AUTH_RESPONSE or resp_id == -1:
            raise AuthenticationError("RCON authentication failed: bad password")

    def send_command(self, command: str) -> str:
        """Send a command and return the response string.

        Uses the "empty packet" trick to detect end of multi-packet responses:
        after the real command, send a follow-up with a known ID. Read responses
        until the follow-up ID appears, concatenating all intermediate bodies.
        """
        if not self._socket:
            raise RCONError("Not connected")

        cmd_id = self._next_id()
        end_id = self._next_id()

        # Send the actual command
        self._send(cmd_id, SERVERDATA_EXECCOMMAND, command)
        # Send an empty follow-up to mark end of response
        self._send(end_id, SERVERDATA_EXECCOMMAND, "")

        # Read until we see the end_id response
        body_parts: list[str] = []
        while True:
            resp_id, _, body = self._recv()
            if resp_id == end_id:
                break
            body_parts.append(body)

        return "".join(body_parts)

    def _send(self, request_id: int, packet_type: int, body: str) -> None:
        """Send a raw RCON packet."""
        if not self._socket:
            raise RCONError("Not connected")
        packet = _pack_packet(request_id, packet_type, body)
        self._socket.sendall(packet)

    def _recv(self) -> tuple[int, int, str]:
        """Receive a single RCON packet."""
        if not self._socket:
            raise RCONError("Not connected")

        # Read the 4-byte size prefix
        size_data = self._recv_exact(4)
        (size,) = struct.unpack("<i", size_data)

        if size < 10:
            raise RCONError(f"Invalid packet size: {size}")
        if size > 4096 + 10:
            raise RCONError(f"Packet too large: {size}")

        # Read the rest of the packet
        body_data = self._recv_exact(size)
        return _unpack_packet(body_data)

    def _recv_exact(self, n: int) -> bytes:
        """Read exactly n bytes from the socket."""
        if not self._socket:
            raise RCONError("Not connected")
        data = bytearray()
        while len(data) < n:
            try:
                chunk = self._socket.recv(n - len(data))
            except socket.timeout as e:
                raise RCONError(f"Socket timeout after receiving {len(data)}/{n} bytes") from e
            if not chunk:
                raise RCONError(f"Connection closed after receiving {len(data)}/{n} bytes")
            data.extend(chunk)
        return bytes(data)

    def close(self) -> None:
        """Close the connection."""
        if self._socket:
            try:
                self._socket.close()
            except OSError:
                pass
            self._socket = None

    def __enter__(self) -> "RCONClient":
        self.connect()
        return self

    def __exit__(self, *args) -> None:
        self.close()
