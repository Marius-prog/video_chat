"""Agora RTC token minting. The App Certificate never leaves this process."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from ..config import Settings
from .errors import RtcUnavailable

ROLE_PUBLISHER = 1


@dataclass(frozen=True)
class RtcCredentials:
    app_id: str
    token: str
    expires_at: int


def build_rtc_token(
    settings: Settings,
    channel: str,
    uid: int,
    *,
    now: Optional[float] = None,
    not_after: Optional[int] = None,
) -> RtcCredentials:
    """Mint a publisher token for ``uid`` on ``channel``.

    ``not_after`` caps the expiry (the room ticket's own expiry), so a token can never outlive
    the permission that justified issuing it.
    """
    if not settings.rtc_configured:
        raise RtcUnavailable("Video service is not configured on this server.")
    try:
        from agora_token_builder import RtcTokenBuilder
    except ImportError as exc:  # pragma: no cover - dependency is pinned in requirements.txt
        raise RtcUnavailable("Video service is not available on this server.") from exc

    expires_at = int(time.time() if now is None else now) + settings.rtc_token_ttl
    if not_after is not None:
        expires_at = min(expires_at, not_after)
    token = RtcTokenBuilder.buildTokenWithUid(
        settings.agora_app_id,
        settings.agora_app_certificate,
        channel,
        uid,
        ROLE_PUBLISHER,
        expires_at,
    )
    return RtcCredentials(app_id=settings.agora_app_id, token=token, expires_at=expires_at)
