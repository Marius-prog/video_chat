"""Small reusable controls so the views stay short."""
from __future__ import annotations

from typing import Callable, Optional

import flet as ft

CARD_COLS = {"xs": 12, "sm": 9, "md": 6, "lg": 5, "xl": 4}


def brand(subtitle: str = "") -> ft.Control:
    rows: list[ft.Control] = [
        ft.Image(src="images/chat-icon.png", width=64, height=64, fit=ft.BoxFit.CONTAIN),
        ft.Text("Secure Video Chat", size=26, weight=ft.FontWeight.BOLD),
    ]
    if subtitle:
        rows.append(ft.Text(subtitle, color=ft.Colors.ON_SURFACE_VARIANT, text_align=ft.TextAlign.CENTER))
    return ft.Column(rows, horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=6)


def text_field(label: str, *, icon: Optional[ft.IconData] = None, password: bool = False, **kwargs) -> ft.TextField:
    return ft.TextField(
        label=label,
        prefix_icon=icon,
        password=password,
        can_reveal_password=password,
        border_radius=10,
        **kwargs,
    )


def error_text() -> ft.Text:
    return ft.Text("", color=ft.Colors.ERROR, size=13, visible=False)


def set_error(control: ft.Text, message: str = "") -> None:
    """Show ``message`` in an :func:`error_text`, or hide it when empty."""
    control.value = message
    control.visible = bool(message)


def centered(content: ft.Control) -> ft.Control:
    """Centre ``content`` in a column that is full-width on phones and narrower on large screens."""
    return ft.ResponsiveRow(
        [ft.Container(content=content, col=CARD_COLS)],
        alignment=ft.MainAxisAlignment.CENTER,
    )


def card(*controls: ft.Control, spacing: int = 14) -> ft.Control:
    return ft.Card(
        content=ft.Container(
            content=ft.Column(list(controls), spacing=spacing, horizontal_alignment=ft.CrossAxisAlignment.STRETCH),
            padding=ft.Padding.all(24),
        ),
        elevation=2,
    )


def notice(text: str, *, warning: bool = True) -> ft.Control:
    color = ft.Colors.AMBER_300 if warning else ft.Colors.GREEN_400
    icon = ft.Icons.WARNING_AMBER if warning else ft.Icons.CHECK_CIRCLE
    return ft.Container(
        content=ft.Row([ft.Icon(icon, color=color), ft.Text(text, expand=True, size=13)], spacing=10),
        padding=ft.Padding.all(12),
        border=ft.Border.all(1, color),
        border_radius=10,
    )


class ActionButton:
    """A button that can show a busy state while a request is in flight."""

    def __init__(self, label: str, on_click: Callable, *, icon: Optional[ft.IconData] = None):
        self.label = label
        self.icon = icon
        self.control = ft.FilledButton(content=label, icon=icon, on_click=on_click, height=44)

    def busy(self, busy: bool, text: Optional[str] = None) -> None:
        self.control.disabled = busy
        self.control.icon = None if busy else self.icon
        self.control.content = (
            ft.Row(
                [ft.ProgressRing(width=16, height=16, stroke_width=2), ft.Text(text or self.label)],
                alignment=ft.MainAxisAlignment.CENTER,
                spacing=8,
            )
            if busy
            else self.label
        )


def toast(page: ft.Page, message: str, *, error: bool = False) -> None:
    page.show_dialog(
        ft.SnackBar(
            content=ft.Text(message),
            bgcolor=ft.Colors.ERROR_CONTAINER if error else None,
        )
    )
