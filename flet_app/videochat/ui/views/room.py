from __future__ import annotations

import flet as ft
import flet_webview as fwv

from .. import components as ui


def room_url(app, grant) -> str:
    """Same-origin URL of the room page. The signed ticket travels in the *fragment*, which browsers never
    send over the network or in the Referer header; the page strips it from the address bar on load."""
    return f"{app.public_base_url()}/room.html#p={grant.ticket}"


async def build(app):
    grant = app.state.grant
    if grant is None:  # opened /room directly, or after leaving
        await app.navigate("/")
        return None

    url = room_url(app, grant)

    async def leave(_=None) -> None:
        await app.end_room()
        await app.navigate("/")

    async def open_in_tab(_=None) -> None:
        # Fallback for browsers that handle camera permission badly inside an embedded frame.
        await app.launcher.launch_url(url, web_only_window_name="_blank")

    return ft.View(
        route="/room",
        appbar=ft.AppBar(
            leading=ft.Icon(ft.Icons.VIDEOCAM),
            title=ft.Text(f"Room {grant.room}"),
            bgcolor=ft.Colors.SURFACE_CONTAINER,
            actions=[
                ft.TextButton("New tab", icon=ft.Icons.OPEN_IN_NEW, on_click=open_in_tab),
                ft.Button("Leave", icon=ft.Icons.CALL_END, bgcolor=ft.Colors.RED_700, color=ft.Colors.WHITE, on_click=leave),
                ft.Container(width=8),
            ],
        ),
        controls=[ft.Container(fwv.WebView(url=url, expand=True), expand=True)],
        padding=0,
    )
