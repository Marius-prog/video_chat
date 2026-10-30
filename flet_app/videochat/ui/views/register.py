from __future__ import annotations

import flet as ft

from ...security import validation
from ...security.validation import ValidationError
from ...services.errors import ServiceError
from .. import components as ui


async def build(app) -> ft.View:
    page = app.page
    username = ui.text_field("Username", icon=ft.Icons.PERSON_OUTLINE, autofocus=True, helper="3-30 letters, digits or underscores")
    email = ui.text_field("Email", icon=ft.Icons.EMAIL)
    password = ui.text_field("Password", icon=ft.Icons.LOCK_OUTLINE, password=True, helper="At least 12 characters")
    confirm = ui.text_field("Confirm password", icon=ft.Icons.LOCK_RESET, password=True)
    error = ui.error_text()
    fields = {"username": username, "email": email, "password": password}

    async def submit(_=None) -> None:
        ui.set_error(error, "")
        for control in (username, email, password, confirm):
            control.error = None
        try:
            name = validation.validate_username(username.value)
            mail = validation.validate_email(email.value)
            secret = validation.validate_password(password.value, username=name, email=mail)
            if secret != confirm.value:
                confirm.error = "Passwords do not match."
                page.update()
                return
        except ValidationError as exc:
            target = fields.get(exc.field or "")
            if target is not None:
                target.error = str(exc)
            else:
                ui.set_error(error, str(exc))
            page.update()
            return

        button.busy(True, "Creating account…")
        page.update()
        try:
            await app.blocking(app.container.accounts.register, name, mail, secret, ip=app.client_ip)
            result = await app.blocking(
                app.container.accounts.login, name, secret, ip=app.client_ip, user_agent=app.user_agent
            )
        except (ServiceError, ValidationError) as exc:
            ui.set_error(error, getattr(exc, "message", None) or str(exc))
        else:
            password.value = confirm.value = ""
            await app.sign_in(result, remember=False)
            return
        finally:
            button.busy(False)
            page.update()

    async def to_login(_=None) -> None:
        await app.navigate("/login")

    for control in (username, email, password, confirm):
        control.on_submit = submit
    button = ui.ActionButton("Create account", submit, icon=ft.Icons.APP_REGISTRATION)

    form = ui.card(
        username,
        email,
        password,
        confirm,
        error,
        button.control,
        ft.Row(
            [ft.Text("Already registered?", color=ft.Colors.ON_SURFACE_VARIANT), ft.TextButton("Sign in", on_click=to_login)],
            alignment=ft.MainAxisAlignment.CENTER,
        ),
    )
    return ft.View(
        route="/register",
        controls=[ui.centered(ft.Column([ui.brand("Create your account"), form], spacing=24, horizontal_alignment=ft.CrossAxisAlignment.CENTER))],
        scroll=ft.ScrollMode.AUTO,
        padding=ft.Padding.all(24),
        vertical_alignment=ft.MainAxisAlignment.CENTER,
    )
