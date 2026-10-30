"""Runtime configuration.

Everything comes from environment variables (optionally loaded from a ``.env``
file next to ``main.py``). Nothing secret is ever hard-coded.
"""
from __future__ import annotations

import logging
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional

APP_DIR = Path(__file__).resolve().parent.parent
ASSETS_DIR = APP_DIR / "assets"

log = logging.getLogger("videochat.config")

MIN_SECRET_KEY_LENGTH = 32
MIN_SCRYPT_N = 2**14


class ConfigError(RuntimeError):
    """The environment holds an unusable or unsafe configuration."""


def _csv(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(",") if part.strip())


def _flag(value: Optional[str], default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _integer(env: Mapping[str, str], name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        number = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc
    if not minimum <= number <= maximum:
        raise ConfigError(f"{name} must be between {minimum} and {maximum}, got {number}")
    return number


@dataclass(frozen=True)
class Settings:
    secret_key: str
    database_url: str
    agora_app_id: str = ""
    agora_app_certificate: str = ""
    host: str = "127.0.0.1"
    port: int = 8550
    allowed_hosts: tuple[str, ...] = ("localhost", "127.0.0.1", "[::1]")
    trusted_proxies: str = ""
    public_base_url: str = ""
    https: bool = False
    session_ttl: int = 7 * 24 * 3600
    session_idle_ttl: int = 24 * 3600
    rtc_token_ttl: int = 3600
    ticket_ttl: int = 6 * 3600
    scrypt_n: int = 2**15
    csp_connect_extra: tuple[str, ...] = ()
    secret_key_is_ephemeral: bool = field(default=False, compare=False)

    @property
    def rtc_configured(self) -> bool:
        return bool(self.agora_app_id and self.agora_app_certificate)

    @classmethod
    def from_env(cls, environ: Optional[Mapping[str, str]] = None, *, load_dotenv: bool = True) -> "Settings":
        if environ is None:
            if load_dotenv:
                _load_dotenv_file(APP_DIR / ".env")
            environ = os.environ
        env = environ

        secret_key = env.get("SECRET_KEY", "").strip()
        ephemeral = False
        if not secret_key:
            secret_key = secrets.token_urlsafe(48)
            ephemeral = True
            log.warning(
                "SECRET_KEY is not set: generated a random one for this process. "
                "Room tickets will stop working on restart; set SECRET_KEY in production."
            )
        elif len(secret_key) < MIN_SECRET_KEY_LENGTH or secret_key.startswith("django-insecure"):
            raise ConfigError(
                f"SECRET_KEY must be at least {MIN_SECRET_KEY_LENGTH} random characters "
                "(generate one with: python -c \"import secrets; print(secrets.token_urlsafe(48))\")"
            )

        data_dir = APP_DIR / "data"
        database_url = env.get("DATABASE_URL", "").strip()
        if not database_url:
            data_dir.mkdir(parents=True, exist_ok=True)
            database_url = f"sqlite:///{data_dir / 'videochat.sqlite3'}"

        public_base_url = env.get("PUBLIC_BASE_URL", "").strip().rstrip("/")
        if public_base_url and not public_base_url.startswith(("http://", "https://")):
            raise ConfigError("PUBLIC_BASE_URL must start with http:// or https://")

        scrypt_n = _integer(env, "SCRYPT_N", 2**15, minimum=MIN_SCRYPT_N, maximum=2**20)
        if scrypt_n & (scrypt_n - 1):
            raise ConfigError("SCRYPT_N must be a power of two")

        allowed_hosts = _csv(env.get("ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]"))
        if not allowed_hosts:
            raise ConfigError("ALLOWED_HOSTS must not be empty")

        return cls(
            secret_key=secret_key,
            secret_key_is_ephemeral=ephemeral,
            database_url=database_url,
            agora_app_id=env.get("AGORA_APP_ID", "").strip(),
            agora_app_certificate=env.get("AGORA_APP_CERTIFICATE", "").strip(),
            host=env.get("HOST", "127.0.0.1").strip() or "127.0.0.1",
            port=_integer(env, "PORT", 8550, minimum=1, maximum=65535),
            allowed_hosts=allowed_hosts,
            trusted_proxies=env.get("TRUSTED_PROXIES", "").strip(),
            public_base_url=public_base_url,
            https=_flag(env.get("HTTPS"), public_base_url.startswith("https://")),
            session_ttl=_integer(env, "SESSION_TTL_SECONDS", 7 * 24 * 3600, minimum=300, maximum=90 * 24 * 3600),
            session_idle_ttl=_integer(env, "SESSION_IDLE_TTL_SECONDS", 24 * 3600, minimum=300, maximum=30 * 24 * 3600),
            rtc_token_ttl=_integer(env, "RTC_TOKEN_TTL_SECONDS", 3600, minimum=60, maximum=24 * 3600),
            ticket_ttl=_integer(env, "ROOM_TICKET_TTL_SECONDS", 6 * 3600, minimum=60, maximum=24 * 3600),
            scrypt_n=scrypt_n,
            csp_connect_extra=_csv(env.get("CSP_CONNECT_SRC_EXTRA", "")),
        )


def _load_dotenv_file(path: Path) -> None:
    if not path.is_file():
        return
    try:
        from dotenv import load_dotenv
    except ImportError:  # python-dotenv is optional at runtime
        log.debug("python-dotenv not installed; ignoring %s", path)
        return
    load_dotenv(path, override=False)
