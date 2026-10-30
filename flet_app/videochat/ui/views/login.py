from __future__ import annotations

import flet as ft

from ...services.errors import ServiceError
from .. import components as ui


async def build(app) -> ft.View:
    page = app.page
    identifier = ui.text_field("Username or email", icon=ft.Icons.PERSON_OUTLINE, autofocus=True)
    password = ui.text_field("Password", icon=ft.Icons.LOCK_OUTLINE, password=True)
    remember = ft.Checkbox(label="Keep me signed in on this device", value=False)
    error = ui.error_text()

    async def submit(_=None) -> None:
        ui.set_error(error, "")
        if not (identifier.value or "").strip() or not password.value:
            ui.set_error(error, "Enter your username or email and your password.")
            page.update()
            return
        button.busy(True, "Signing in…")
        page.update()
        try:
            result = await app.blocking(
                app.container.accounts.login,
                identifier.value,
                password.value,
                ip=app.client_ip,
                user_agent=app.user_agent,
            )
        except ServiceError as exc:
            ui.set_error(error, exc.message)
            password.value = ""
        else:
            password.value = ""
            await app.sign_in(result, remember=bool(remember.value))
            return
        finally:
            button.busy(False)
            page.update()

    async def to_register(_=None) -> None:
        await app.navigate("/register")

    identifier.on_submit = submit
    password.on_submit = submit
    button = ui.ActionButton("Sign in", submit, icon=ft.Icons.LOGIN)

    form = ui.card(
        identifier,
        password,
        remember,
        error,
        button.control,
        ft.Row(
            [ft.Text("New here?", color=ft.Colors.ON_SURFACE_VARIANT), ft.TextButton("Create an account", on_click=to_register)],
            alignment=ft.MainAxisAlignment.CENTER,
        ),
    )
    return ft.View(
        route="/login",
        controls=[ui.centered(ft.Column([ui.brand("Sign in to start or join a room"), form], spacing=24, horizontal_alignment=ft.CrossAxisAlignment.CENTER))],
        scroll=ft.ScrollMode.AUTO,
        padding=ft.Padding.all(24),
        vertical_alignment=ft.MainAxisAlignment.CENTER,
    )
