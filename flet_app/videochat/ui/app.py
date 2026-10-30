"""The Flet application: routing, login state and the bridge to the services.

Everything here runs on the server. The browser only receives rendered UI; the login token and the room
ticket live in Flet's server-side session store. (If the user ticks "keep me signed in" the login token is
also kept in the browser's local storage so a new tab can resume it - see ``README`` for the trade-off.)

Flet runs *synchronous* handlers directly on the event loop, so every handler here is ``async`` and pushes
blocking work (password hashing, database access) through :meth:`VideoChatApp.blocking`.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Optional
from urllib.parse import urlsplit

import flet as ft

from ..container import Container
from ..services.accounts import LoginResult, Principal
from ..services.rooms import JoinGrant
from . import theme

log = logging.getLogger("videochat.ui")

TOKEN_KEY = "videochat.login"
PROTECTED = frozenset({"/", "/profile", "/room"})
PUBLIC_ONLY = frozenset({"/login", "/register"})


class SessionState:
    """Per-browser-session state (server side, in Flet's session store)."""

    def __init__(self, page: ft.Page):
        self._store = page.session.store

    def _get(self, key: str):
        return self._store.get(key)

    def _set(self, key: str, value) -> None:
        self._store.set(key, value)

    principal: Optional[Principal] = property(lambda s: s._get("principal"), lambda s, v: s._set("principal", v))
    token: Optional[str] = property(lambda s: s._get("token"), lambda s, v: s._set("token", v))
    grant: Optional[JoinGrant] = property(lambda s: s._get("grant"), lambda s, v: s._set("grant", v))

    def clear(self) -> None:
        self.principal = None
        self.token = None
        self.grant = None


class VideoChatApp:
    def __init__(self, page: ft.Page, container: Container):
        self.page = page
        self.container = container
        self.settings = container.settings
        self.state = SessionState(page)
        # Services register themselves with the current page on construction.
        self.prefs = ft.SharedPreferences()
        self.launcher = ft.UrlLauncher()

    # -- lifecycle ----------------------------------------------------------

    async def start(self) -> None:
        theme.apply(self.page)
        self.page.on_route_change = self._on_route_change
        await self.blocking(self.container.housekeeping)
        await self._restore_login()
        await self.render(self.page.route)

    async def _on_route_change(self, event: ft.RouteChangeEvent) -> None:
        await self.render(event.route)

    # -- helpers used by the views -----------------------------------------

    async def blocking(self, fn: Callable, *args, **kwargs):
        """Run blocking code (scrypt, SQL) in a worker thread so one user never stalls the others."""
        return await asyncio.to_thread(fn, *args, **kwargs)

    async def navigate(self, route: str) -> None:
        await self.page.push_route(route)

    @property
    def client_ip(self) -> str:
        return self.page.client_ip or ""

    @property
    def user_agent(self) -> str:
        return self.page.client_user_agent or ""

    def public_base_url(self) -> str:
        """Origin the browser uses to reach this server, e.g. ``https://chat.example.com``."""
        if self.settings.public_base_url:
            return self.settings.public_base_url
        parts = urlsplit(self.page.url or "")
        scheme = {"ws": "http", "wss": "https"}.get(parts.scheme, parts.scheme or "http")
        return f"{scheme}://{parts.netloc}"

    # -- authentication -----------------------------------------------------

    async def sign_in(self, result: LoginResult, *, remember: bool) -> None:
        self.state.principal = result.principal
        self.state.token = result.token
        if remember:
            try:
                await self.prefs.set(TOKEN_KEY, result.token)
            except Exception:  # storage is a convenience; never block a login on it
                log.warning("could not persist login token in the browser", exc_info=True)
        await self.navigate("/")

    async def sign_out(self, *, everywhere: bool = False) -> None:
        principal, token = self.state.principal, self.state.token
        if principal:
            if everywhere:
                await self.blocking(self.container.accounts.logout_all, principal.user.id)
            else:
                await self.blocking(self.container.accounts.logout, token)
        await self._drop_login()
        await self.navigate("/login")

    async def require_session(self) -> bool:
        """True while the current login is still valid; otherwise clears it and goes to the sign-in page."""
        principal = self.state.principal
        if principal and await self.blocking(
            self.container.accounts.session_active, principal.session_id, principal.user.id
        ):
            return True
        await self._drop_login()
        await self.navigate("/login")
        return False

    async def _drop_login(self) -> None:
        await self.end_room()
        self.state.clear()
        try:
            await self.prefs.remove(TOKEN_KEY)
        except Exception:
            log.debug("could not clear stored login token", exc_info=True)

    async def _restore_login(self) -> None:
        if self.state.principal:
            return
        try:
            token = await self.prefs.get(TOKEN_KEY)
        except Exception:
            log.debug("could not read stored login token", exc_info=True)
            return
        if not token:
            return
        principal = await self.blocking(self.container.accounts.resolve, token)
        if principal is None:
            try:
                await self.prefs.remove(TOKEN_KEY)
            except Exception:
                pass
            return
        self.state.principal = principal
        self.state.token = token

    # -- rooms --------------------------------------------------------------

    async def end_room(self) -> None:
        grant, principal = self.state.grant, self.state.principal
        self.state.grant = None
        if grant and principal:
            await self.blocking(self.container.rooms.leave, grant.room, grant.uid, principal.user.id)

    # -- rendering ----------------------------------------------------------

    async def render(self, route: Optional[str]) -> None:
        from .views import lobby, login, profile, register, room  # views import this module's helpers

        path = (urlsplit(route or "/").path.rstrip("/")) or "/"
        builders: dict[str, Callable[["VideoChatApp"], Awaitable[ft.View]]] = {
            "/login": login.build,
            "/register": register.build,
            "/": lobby.build,
            "/profile": profile.build,
            "/room": room.build,
        }
        if path not in builders:
            await self.navigate("/")
            return
        if path in PROTECTED and not await self.require_session():
            return
        if path in PUBLIC_ONLY and self.state.principal:
            await self.navigate("/")
            return
        if path != "/room":
            await self.end_room()

        view = await builders[path](self)
        if view is None:  # a builder redirected elsewhere
            return
        self.page.views.clear()
        self.page.views.append(view)
        self.page.update()


def make_main(container: Container):
    async def main(page: ft.Page) -> None:
        await VideoChatApp(page, container).start()

    return main
