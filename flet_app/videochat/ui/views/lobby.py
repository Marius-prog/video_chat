from __future__ import annotations

import flet as ft

from ...security import validation
from ...security.validation import ValidationError
from ...services.errors import ServiceError
from .. import components as ui


def app_bar(app, title: str, *, back: bool = False) -> ft.AppBar:
    async def go_home(_=None) -> None:
        await app.navigate("/")

    async def go_profile(_=None) -> None:
        await app.navigate("/profile")

    async def sign_out(_=None) -> None:
        await app.sign_out()

    # Text labels rather than icon-only buttons: Flet 1.0.5 does not expose an IconButton's tooltip as its
    # accessible name, so screen readers would announce an unnamed button.
    actions = []
    if back:
        actions.append(ft.TextButton("Lobby", icon=ft.Icons.ARROW_BACK, on_click=go_home))
    else:
        actions.append(ft.TextButton("Profile", icon=ft.Icons.MANAGE_ACCOUNTS, on_click=go_profile))
    actions.append(ft.TextButton("Sign out", icon=ft.Icons.LOGOUT, on_click=sign_out))
    actions.append(ft.Container(width=8))
    return ft.AppBar(
        leading=ft.Icon(ft.Icons.VIDEOCAM),
        title=ft.Text(title),
        actions=actions,
        bgcolor=ft.Colors.SURFACE_CONTAINER,
    )


async def build(app) -> ft.View:
    page = app.page
    principal = app.state.principal
    recent = await app.blocking(app.container.rooms.recent_rooms, principal.user.id)

    room = ui.text_field("Room name", icon=ft.Icons.MEETING_ROOM, autofocus=True, hint_text="e.g. TEAM-STANDUP", helper="Letters, digits, - and _ (created if it does not exist)")
    name = ui.text_field("Display name", icon=ft.Icons.BADGE, value=principal.user.username)
    error = ui.error_text()

    async def join(_=None) -> None:
        ui.set_error(error, "")
        room.error = name.error = None
        try:
            room_name = validation.validate_room_name(room.value)
            display = validation.validate_display_name(name.value)
        except ValidationError as exc:
            target = {"room": room, "name": name}.get(exc.field or "")
            if target is not None:
                target.error = str(exc)
            else:
                ui.set_error(error, str(exc))
            page.update()
            return
        if not await app.require_session():
            return
        button.busy(True, "Joining…")
        page.update()
        try:
            grant = await app.blocking(
                app.container.rooms.join, principal.user.id, principal.session_id, room_name, display
            )
        except ServiceError as exc:
            ui.set_error(error, exc.message)
        else:
            app.state.grant = grant
            await app.navigate("/room")
            return
        finally:
            button.busy(False)
            page.update()

    def fill(room_name: str):
        def handler(_=None) -> None:
            room.value = room_name
            page.update()

        return handler

    room.on_submit = join
    name.on_submit = join
    button = ui.ActionButton("Join room", join, icon=ft.Icons.VIDEO_CALL)
    if not app.settings.rtc_configured:
        button.control.disabled = True

    controls: list[ft.Control] = [
        ft.Text(f"Welcome, {principal.user.username}", size=20, weight=ft.FontWeight.W_600),
        room,
        name,
        error,
        button.control,
    ]
    if not app.settings.rtc_configured:
        controls.insert(1, ui.notice("Video is not configured on this server. Set AGORA_APP_ID and AGORA_APP_CERTIFICATE and restart."))
    if recent:
        controls += [
            ft.Divider(),
            ft.Text("Recent rooms", color=ft.Colors.ON_SURFACE_VARIANT),
            ft.Row(
                [ft.Chip(label=ft.Text(r.name), leading=ft.Icon(ft.Icons.HISTORY), on_click=fill(r.name)) for r in recent],
                wrap=True,
                spacing=8,
            ),
        ]
    return ft.View(
        route="/",
        appbar=app_bar(app, "Secure Video Chat"),
        controls=[ft.Container(ui.centered(ui.card(*controls)), padding=ft.Padding.all(24))],
        scroll=ft.ScrollMode.AUTO,
        padding=0,
    )
