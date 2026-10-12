"""Второй фактор админки: TOTP по RFC 6238 (Google Authenticator и аналоги)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import struct
import time


def code(secret_b32: str, at: float | None = None, step: int = 30) -> str:
    key = base64.b32decode(secret_b32.upper() + "=" * (-len(secret_b32) % 8))
    counter = int((at or time.time()) // step)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return f"{value % 1_000_000:06d}"


def verify(secret_b32: str, given: str, at: float | None = None) -> bool:
    now = at or time.time()
    given = (given or "").strip()
    return any(hmac.compare_digest(code(secret_b32, now + d * 30), given) for d in (-1, 0, 1))
