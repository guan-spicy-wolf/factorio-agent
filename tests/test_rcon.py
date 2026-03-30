"""Unit tests for the RCON protocol client.

Tests packet encoding/decoding without a real Factorio server.
"""

import struct
import pytest

from agent.rcon import (
    _pack_packet,
    _unpack_packet,
    RCONClient,
    RCONError,
    SERVERDATA_AUTH,
    SERVERDATA_EXECCOMMAND,
    SERVERDATA_RESPONSE_VALUE,
    SERVERDATA_AUTH_RESPONSE,
)


class TestPacketEncoding:
    """Test RCON packet packing and unpacking."""

    def test_pack_empty_body(self):
        packet = _pack_packet(1, SERVERDATA_EXECCOMMAND, "")
        # size(4) + id(4) + type(4) + body("" + \x00) + pad(\x00) = 10
        size = struct.unpack_from("<i", packet, 0)[0]
        assert size == 10

    def test_pack_with_body(self):
        packet = _pack_packet(42, SERVERDATA_EXECCOMMAND, "hello")
        size = struct.unpack_from("<i", packet, 0)[0]
        # 4 + 4 + 5 + 2 = 15
        assert size == 15
        # Total packet length = 4 (size field) + size
        assert len(packet) == 4 + size

    def test_pack_unpack_roundtrip(self):
        original_id = 123
        original_type = SERVERDATA_AUTH
        original_body = "test password"

        packet = _pack_packet(original_id, original_type, original_body)
        # Skip the 4-byte size prefix for unpacking
        req_id, pkt_type, body = _unpack_packet(packet[4:])

        assert req_id == original_id
        assert pkt_type == original_type
        assert body == original_body

    def test_pack_unpack_unicode(self):
        packet = _pack_packet(1, SERVERDATA_EXECCOMMAND, "你好世界")
        req_id, pkt_type, body = _unpack_packet(packet[4:])
        assert body == "你好世界"

    def test_unpack_too_short(self):
        with pytest.raises(RCONError, match="too short"):
            _unpack_packet(b"\x00" * 5)

    def test_pack_auth_packet(self):
        packet = _pack_packet(1, SERVERDATA_AUTH, "secret")
        # Verify type field
        pkt_type = struct.unpack_from("<i", packet, 8)[0]
        assert pkt_type == SERVERDATA_AUTH


class TestPacketFormat:
    """Verify the wire format matches Source RCON spec."""

    def test_packet_structure(self):
        packet = _pack_packet(7, SERVERDATA_EXECCOMMAND, "test")
        size, req_id, pkt_type = struct.unpack_from("<iii", packet, 0)

        assert req_id == 7
        assert pkt_type == SERVERDATA_EXECCOMMAND

        # Body starts at offset 12, followed by two null bytes
        body_start = 12
        body_end = len(packet) - 2
        body = packet[body_start:body_end].decode("utf-8")
        assert body == "test"

        # Last two bytes are null terminators
        assert packet[-2:] == b"\x00\x00"


class TestRCONClient:
    """Test RCONClient without a real server."""

    def test_send_command_not_connected(self):
        client = RCONClient()
        with pytest.raises(RCONError, match="Not connected"):
            client.send_command("test")

    def test_close_when_not_connected(self):
        client = RCONClient()
        # Should not raise
        client.close()

    def test_default_parameters(self):
        client = RCONClient()
        assert client.host == "127.0.0.1"
        assert client.port == 27015
        assert client.password == ""
        assert client.timeout == 10.0

    def test_custom_parameters(self):
        client = RCONClient(
            host="192.168.1.1",
            port=25575,
            password="secret",
            timeout=5.0,
        )
        assert client.host == "192.168.1.1"
        assert client.port == 25575
        assert client.password == "secret"
        assert client.timeout == 5.0
