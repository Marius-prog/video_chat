"""Look and feel."""
from __future__ import annotations

import flet as ft

SEED = ft.Colors.INDIGO


def apply(page: ft.Page) -> None:
    page.title = "Secure Video Chat"
    page.theme_mode = ft.ThemeMode.DARK
    page.theme = ft.Theme(color_scheme_seed=SEED)
    page.dark_theme = ft.Theme(color_scheme_seed=SEED)
    page.padding = 0
