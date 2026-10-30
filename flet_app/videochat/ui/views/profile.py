from __future__ import annotations

from datetime import datetime, timezone

import flet as ft

from ...security.validation import ValidationError
from ...services.errors import ServiceError
from .. import components as ui
from .lobby import app_bar


def _date(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d")


async def build(app) -> ft.View:
    page = app.page
    principal = app.state.principal
    user = principal.user
    owned = await app.blocking(app.container.rooms.owned_rooms, user.id)

    current = ui.text_field("Current password", icon=ft.Icons.LOCK_OUTLINE, password=True)
    new = ui.text_field("New password", icon=ft.Icons.KEY, password=True, helper="At least 12 characters")
    confirm = ui.text_field("Confirm new password", icon=ft.Icons.LOCK_RESET, password=True)
    error = ui.error_text()

    async def change(_=None) -> None:
        ui.set_error(error, "")
        new.error = confirm.error = None
        if new.value != confirm.value:
            confirm.error = "Passwords do not match."
            page.update()
            return
        if not await app.require_session():
            return
        button.busy(True, "Saving…")
        page.update()
        try:
            await app.blocking(
                app.container.accounts.change_password,
                user.id,
                current.value or "",
                new.value or "",
                keep_session_id=principal.session_id,
            )
        except ValidationError as exc:
            new.error = str(exc)
        except ServiceError as exc:
            ui.set_error(error, exc.message)
        else:
            current.value = new.value = confirm.value = ""
            ui.toast(page, "Password changed. Your other devices were signed out.")
        finally:
            button.busy(False)
            page.update()

    async def sign_out_everywhere(_=None) -> None:
        await app.sign_out(everywhere=True)

    button = ui.ActionButton("Change password", change, icon=ft.Icons.KEY)

    details = ui.card(
        ft.Text("Account", size=18, weight=ft.FontWeight.W_600),
        ft.ListTile(title=ft.Text(user.username), subtitle=ft.Text("Username"), leading=ft.Icon(ft.Icons.PERSON_OUTLINE)),
        ft.ListTile(title=ft.Text(user.email), subtitle=ft.Text("Email"), leading=ft.Icon(ft.Icons.EMAIL)),
        ft.ListTile(title=ft.Text(_date(user.created_at)), subtitle=ft.Text("Member since"), leading=ft.Icon(ft.Icons.HISTORY)),
        spacing=4,
    )
    rooms = ui.card(
        ft.Text("Rooms you created", size=18, weight=ft.FontWeight.W_600),
        *(
            [ft.ListTile(title=ft.Text(r.name), subtitle=ft.Text(f"Created {_date(r.last_joined_at)}"), leading=ft.Icon(ft.Icons.MEETING_ROOM)) for r in owned]
            or [ft.Text("None yet - joining a room that does not exist creates it.", color=ft.Colors.ON_SURFACE_VARIANT)]
        ),
        spacing=4,
    )
    security = ui.card(
        ft.Text("Security", size=18, weight=ft.FontWeight.W_600),
        current,
        new,
        confirm,
        error,
        button.control,
        ft.Divider(),
        ft.OutlinedButton("Sign out of all devices", icon=ft.Icons.LOGOUT, on_click=sign_out_everywhere),
    )
    return ft.View(
        route="/profile",
        appbar=app_bar(app, "Profile", back=True),
        controls=[ft.Container(ui.centered(ft.Column([details, rooms, security], spacing=16)), padding=ft.Padding.all(24))],
        scroll=ft.ScrollMode.AUTO,
        padding=0,
    )
