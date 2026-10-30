"""Strict input validation shared by the UI, the services and the HTTP API.

Every function takes untrusted input and returns the *normalised* value or
raises :class:`ValidationError` with a message that is safe to show to users.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Optional

MAX_RAW_LENGTH = 512  # reject absurd inputs before running any regex on them

USERNAME_RE = re.compile(r"[a-z0-9_]{3,30}")
ROOM_RE = re.compile(r"[A-Z0-9_-]{1,64}")
EMAIL_RE = re.compile(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+")
DISPLAY_NAME_RE = re.compile(r"[\w .'-]{2,50}")

PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 128
MAX_UID = 2**32 - 1

_COMMON_PASSWORDS = frozenset(
    """
    password123456 passwordpassword password1234 password12345 123456789012 1234567890123
    qwertyuiop12 qwertyuiopas qwerty123456 qwertyuiop123 iloveyou1234 letmein123456
    welcome12345 administrator changeme1234 abcdefghijkl abc123456789 111111111111
    000000000000 123123123123 1q2w3e4r5t6y 1qaz2wsx3edc zaq12wsxcde3 football12345
    monkey123456 dragon123456 master123456 sunshine1234 princess1234 trustno1trustno1
    """.split()
)


class ValidationError(ValueError):
    """User-correctable input problem. ``str(error)`` is safe to display."""

    def __init__(self, message: str, field: Optional[str] = None):
        super().__init__(message)
        self.field = field


def _text(raw: Any, field: str, label: str) -> str:
    if raw is None:
        raise ValidationError(f"{label} is required.", field)
    if not isinstance(raw, str):
        raise ValidationError(f"{label} must be text.", field)
    if len(raw) > MAX_RAW_LENGTH:
        raise ValidationError(f"{label} is too long.", field)
    return raw


def _reject_control_chars(value: str, field: str, label: str) -> None:
    # Category "C*" covers control, format (zero-width, bidi overrides), surrogates, private use.
    if any(unicodedata.category(ch).startswith("C") for ch in value):
        raise ValidationError(f"{label} contains invalid characters.", field)


def _ascii_fold(raw: str, upper: bool) -> str:
    """NFKC-normalise, trim and case-fold, refusing anything that is not ASCII *before* folding.

    Folding first would let non-ASCII characters collide with ASCII ones ('ß'.upper() == 'SS').
    """
    value = unicodedata.normalize("NFKC", raw).strip()
    if not value.isascii():
        return ""
    return value.upper() if upper else value.lower()


def validate_username(raw: Any) -> str:
    value = _ascii_fold(_text(raw, "username", "Username"), upper=False)
    if not value:
        raise ValidationError("Username is required.", "username")
    if not USERNAME_RE.fullmatch(value):
        raise ValidationError(
            "Username must be 3-30 characters: letters, digits and underscores only.", "username"
        )
    return value


def validate_email(raw: Any) -> str:
    value = unicodedata.normalize("NFKC", _text(raw, "email", "Email")).strip().lower()
    if not value:
        raise ValidationError("Email is required.", "email")
    if len(value) > 254 or not EMAIL_RE.fullmatch(value) or ".." in value:
        raise ValidationError("Enter a valid email address.", "email")
    return value


def validate_password(raw: Any, *, username: str = "", email: str = "") -> str:
    """Return the password unchanged (it is never trimmed) if it meets the policy."""
    value = _text(raw, "password", "Password")
    if len(value) < PASSWORD_MIN_LENGTH:
        raise ValidationError(f"Password must be at least {PASSWORD_MIN_LENGTH} characters.", "password")
    if len(value) > PASSWORD_MAX_LENGTH:
        raise ValidationError(f"Password must be at most {PASSWORD_MAX_LENGTH} characters.", "password")
    if not value.strip():
        raise ValidationError("Password cannot be only spaces.", "password")
    lowered = value.lower()
    if value.isdigit():
        raise ValidationError("Password cannot be only digits.", "password")
    if len(set(lowered)) < 5:
        raise ValidationError("Password is too repetitive.", "password")
    if lowered in _COMMON_PASSWORDS:
        raise ValidationError("That password is too common.", "password")
    for needle in (username, email.split("@")[0] if email else ""):
        if len(needle) >= 3 and needle.lower() in lowered:
            raise ValidationError("Password must not contain your username or email.", "password")
    return value


def validate_room_name(raw: Any) -> str:
    text = _text(raw, "room", "Room name")
    value = _ascii_fold(text, upper=True)
    if not unicodedata.normalize("NFKC", text).strip():
        raise ValidationError("Room name is required.", "room")
    if not ROOM_RE.fullmatch(value):
        raise ValidationError(
            "Room name must be 1-64 characters: letters, digits, '-' and '_' only.", "room"
        )
    return value


def validate_display_name(raw: Any) -> str:
    value = unicodedata.normalize("NFKC", _text(raw, "name", "Display name"))
    _reject_control_chars(value.replace(" ", ""), "name", "Display name")
    value = " ".join(value.split())
    if not DISPLAY_NAME_RE.fullmatch(value) or not any(ch.isalnum() for ch in value):
        raise ValidationError(
            "Display name must be 2-50 characters: letters, digits, spaces and . ' _ - only.", "name"
        )
    return value


def validate_uid(raw: Any) -> int:
    if isinstance(raw, bool):
        raise ValidationError("UID must be an integer.", "uid")
    try:
        number = int(raw)
    except (TypeError, ValueError):
        raise ValidationError("UID must be an integer.", "uid") from None
    if not 1 <= number <= MAX_UID:
        raise ValidationError("UID is out of range.", "uid")
    return number
