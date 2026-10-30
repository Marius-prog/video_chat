"""Password hashing with scrypt (standard library, memory-hard).

Stored format: ``scrypt$<n>$<r>$<p>$<salt>$<hash>`` (salt/hash urlsafe-base64, no padding).
The cost parameters live in the string, so they can be raised later and old
hashes keep verifying; :func:`needs_rehash` tells you when to upgrade one.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import unicodedata
from functools import lru_cache

SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1
SALT_BYTES = 16
KEY_BYTES = 32
_MAXMEM = 128 * 1024 * 1024  # hashlib's default (32 MiB) is too small for n=2**15, r=8

# Bounds applied to parameters read back from storage, so a corrupted row
# cannot make verification allocate gigabytes.
_MAX_N = 2**20
_MAX_R = 32
_MAX_P = 16


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _prepare(password: str) -> bytes:
    return unicodedata.normalize("NFKC", password).encode("utf-8")


def hash_password(password: str, *, n: int = SCRYPT_N, r: int = SCRYPT_R, p: int = SCRYPT_P) -> str:
    salt = secrets.token_bytes(SALT_BYTES)
    digest = hashlib.scrypt(_prepare(password), salt=salt, n=n, r=r, p=p, maxmem=_MAXMEM, dklen=KEY_BYTES)
    return f"scrypt${n}${r}${p}${_b64e(salt)}${_b64e(digest)}"


def _parse(stored: str):
    if not isinstance(stored, str):
        raise ValueError("stored hash must be text")
    scheme, n, r, p, salt, digest = stored.split("$")
    if scheme != "scrypt":
        raise ValueError("unknown scheme")
    n, r, p = int(n), int(r), int(p)
    if not (2 <= n <= _MAX_N and n & (n - 1) == 0 and 1 <= r <= _MAX_R and 1 <= p <= _MAX_P):
        raise ValueError("parameters out of bounds")
    return n, r, p, _b64d(salt), _b64d(digest)


def verify_password(password: str, stored: str) -> bool:
    """Constant-time check. Malformed hashes simply fail verification."""
    try:
        n, r, p, salt, expected = _parse(stored)
        actual = hashlib.scrypt(_prepare(password), salt=salt, n=n, r=r, p=p, maxmem=_MAXMEM, dklen=len(expected))
    except (ValueError, TypeError, OverflowError):
        return False
    return hmac.compare_digest(actual, expected)


def needs_rehash(stored: str, *, n: int = SCRYPT_N, r: int = SCRYPT_R, p: int = SCRYPT_P) -> bool:
    try:
        sn, sr, sp, _, _ = _parse(stored)
    except (ValueError, TypeError):
        return True
    return (sn, sr, sp) != (n, r, p)


@lru_cache(maxsize=8)
def dummy_hash(n: int = SCRYPT_N) -> str:
    """A throw-away hash used to burn the same CPU time when a user does not exist."""
    return hash_password("not-a-real-password", n=n)
