"""Blueprint string codec: ``"0" + base64(zlib(json))``."""

from __future__ import annotations

import base64
import json
import zlib
from typing import Any

FORMAT_VERSION_CHAR = "0"


def decode(s: str) -> dict[str, Any]:
    s = s.strip()
    if not s or s[0] != FORMAT_VERSION_CHAR:
        raise ValueError("not a blueprint string (expected leading '0')")
    try:
        raw = zlib.decompress(base64.b64decode(s[1:], validate=True))
    except (ValueError, zlib.error) as exc:
        raise ValueError(f"corrupt blueprint string: {exc}") from exc
    obj = json.loads(raw)
    if not isinstance(obj, dict):
        raise ValueError("blueprint JSON root is not an object")
    return obj


def encode(obj: dict[str, Any]) -> str:
    raw = json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return FORMAT_VERSION_CHAR + base64.b64encode(zlib.compress(raw, 9)).decode("ascii")


def pack_version(version: str) -> int:
    """``"2.0.28"`` -> ``major<<48 | minor<<32 | patch<<16 | build`` (build defaults to 0)."""
    parts = [int(p) for p in version.split(".")]
    if not 1 <= len(parts) <= 4 or any(not 0 <= p < 1 << 16 for p in parts):
        raise ValueError(f"bad version string {version!r}")
    parts += [0] * (4 - len(parts))
    major, minor, patch, build = parts
    return major << 48 | minor << 32 | patch << 16 | build


def unpack_version(value: int) -> tuple[int, int, int, int]:
    return (value >> 48) & 0xFFFF, (value >> 32) & 0xFFFF, (value >> 16) & 0xFFFF, value & 0xFFFF
