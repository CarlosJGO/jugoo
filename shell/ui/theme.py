"""Validated TOML themes, GTK CSS compilation, and Hyprland export."""

from __future__ import annotations

import re
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gio", "2.0")
gi.require_version("Gtk", "3.0")

from gi.repository import Gdk, Gio, GLib, Gtk

from ..eventbus import EventBus

THEME_CHANGED = "theme_changed"
THEME_RELOAD_DEBOUNCE_MS = 160

_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
_RADIUS_PROPERTY = re.compile(r"(border-radius\s*:\s*)([^;]+)(;)")
_PIXEL_VALUE = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)px")


@dataclass(frozen=True)
class ThemeColors:
    background: str
    surface: str
    surface_alt: str
    surface_hover: str
    surface_active: str
    primary: str
    secondary: str
    accent: str
    text: str
    text_muted: str
    text_disabled: str
    border: str
    border_active: str
    success: str
    warning: str
    error: str
    info: str


@dataclass(frozen=True)
class ThemeEffects:
    blur: int
    opacity: float


@dataclass(frozen=True)
class ThemeShape:
    radius: int


@dataclass(frozen=True)
class ThemeAnimation:
    enabled: bool
    duration: int


@dataclass(frozen=True)
class Theme:
    key: str
    name: str
    colors: ThemeColors
    effects: ThemeEffects
    shape: ThemeShape
    animation: ThemeAnimation

    @property
    def spectrum_palette(self) -> tuple[str, str, str]:
        return (self.colors.primary, self.colors.secondary, self.colors.accent)


_active_theme: Theme | None = None


def active_theme() -> Theme | None:
    """Return the currently applied theme for non-widget animation helpers."""
    return _active_theme


def color_to_rgb(color: str) -> tuple[float, float, float]:
    """Convert a validated #RRGGBB color to normalized Cairo channels."""
    red, green, blue = _rgb(color)
    return (red / 255.0, green / 255.0, blue / 255.0)


def discover_themes(directory: Path) -> dict[str, Path]:
    if not directory.is_dir():
        return {}
    return {
        path.stem: path
        for path in sorted(directory.glob("*.toml"))
        if path.is_file() and not path.name.startswith(".")
    }


def load_theme(path: Path, *, key: str | None = None) -> Theme:
    try:
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
        raise ValueError(f"cannot read theme {path}: {error}") from error

    meta = _section(payload, "meta")
    colors_data = _section(payload, "colors")
    effects = _section(payload, "effects")
    shape = _section(payload, "shape")
    animation = _section(payload, "animation")

    color_values: dict[str, str] = {}
    for field_name in ThemeColors.__dataclass_fields__:
        value = colors_data.get(field_name)
        if not isinstance(value, str) or not _HEX_COLOR.fullmatch(value):
            raise ValueError(f"colors.{field_name} must be a #RRGGBB color")
        color_values[field_name] = value.upper()

    blur = _integer(effects, "blur", minimum=0, maximum=64)
    opacity = _number(effects, "opacity", minimum=0.2, maximum=1.0)
    radius = _integer(shape, "radius", minimum=0, maximum=48)
    duration = _integer(animation, "duration", minimum=0, maximum=5000)
    enabled = animation.get("enabled")
    if not isinstance(enabled, bool):
        raise ValueError("animation.enabled must be a boolean")

    name = meta.get("name", path.stem)
    if not isinstance(name, str) or not name.strip():
        raise ValueError("meta.name must be a non-empty string")

    return Theme(
        key=key or path.stem,
        name=name.strip(),
        colors=ThemeColors(**color_values),
        effects=ThemeEffects(blur=blur, opacity=opacity),
        shape=ThemeShape(radius=radius),
        animation=ThemeAnimation(enabled=enabled, duration=duration),
    )


def compile_gtk_css(theme: Theme, structural_css: str) -> str:
    """Compile semantic colors and scalable shape values into GTK3 CSS."""
    opacity = theme.effects.opacity
    colors = theme.colors
    definitions = {
        "theme_background": colors.background,
        "theme_surface": colors.surface,
        "theme_surface_alt": colors.surface_alt,
        "theme_surface_hover": colors.surface_hover,
        "theme_surface_active": colors.surface_active,
        "theme_primary": colors.primary,
        "theme_secondary": colors.secondary,
        "theme_accent": colors.accent,
        "theme_text": colors.text,
        "theme_text_muted": colors.text_muted,
        "theme_text_disabled": colors.text_disabled,
        "theme_border": colors.border,
        "theme_border_active": colors.border_active,
        "theme_success": colors.success,
        "theme_warning": colors.warning,
        "theme_error": colors.error,
        "theme_info": colors.info,
        "shell_surface_bg": _rgba(colors.surface, opacity * 0.92),
        "shell_bar_bg": _rgba(colors.background, opacity),
        "shell_bar_border": _rgba(colors.border, 0.72),
        "shell_surface_bg_elevated": _rgba(colors.surface_alt, min(1.0, opacity + 0.02)),
        "shell_surface_bg_panel": _rgba(colors.surface_alt, min(1.0, opacity + 0.04)),
        "shell_text_primary": colors.text,
        "shell_text_secondary": colors.text_muted,
        "shell_text_muted": colors.text_disabled,
        "shell_accent": colors.primary,
        "shell_accent_soft": _rgba(colors.primary, 0.22),
        "shell_secondary": colors.secondary,
        "shell_special": colors.accent,
        "shell_special_soft": _rgba(colors.accent, 0.12),
        "shell_special_active": _rgba(colors.accent, 0.22),
        "shell_border_subtle": _rgba(colors.border, 0.68),
        "shell_border_medium": _rgba(colors.border, 0.84),
        "shell_border_strong": colors.border_active,
        "shell_hover_bg": colors.surface_hover,
        "shell_hover_bg_soft": _rgba(colors.surface_hover, 0.82),
        "shell_hover_bg_faint": _rgba(colors.surface_hover, 0.56),
        "shell_danger": colors.error,
        "shell_success": colors.success,
        "shell_warning": colors.warning,
        "shell_info": colors.info,
        "shell_on_accent": colors.text,
        "shell_backdrop": _rgba(colors.background, 0.72),
        "shell_shadow_soft": _rgba(colors.background, 0.35),
        "shell_shadow": _rgba(colors.background, 0.55),
        "shell_shadow_strong": _rgba(colors.background, 0.78),
        "shell_glass_highlight": _rgba(colors.text, 0.16),
        "shell_temp_cold": colors.info,
        "shell_temp_normal": colors.success,
        "shell_temp_warm": colors.warning,
        "shell_temp_hot": colors.error,
    }
    prelude = [
        f"/* Generated from theme: {theme.key}. Do not define palette values below. */"
    ]
    prelude.extend(
        f"@define-color {name} {value};" for name, value in definitions.items()
    )
    return "\n".join(prelude) + "\n\n" + _scale_radii(structural_css, theme.shape.radius)


def export_hypr_theme(theme: Theme, destination: Path) -> Path:
    """Write the Hyprland override atomically from the same semantic theme."""
    c = theme.colors
    opacity = theme.effects.opacity
    content = f"""-- Generated by Jugoo from themes/{theme.key}.toml. Do not edit.
local function apply()
    hl.config({{
        general = {{
            col = {{
                active_border = {{ colors = {{ "{_hypr(c.primary)}", "{_hypr(c.accent)}" }}, angle = 45 }},
                inactive_border = "{_hypr(c.border)}",
            }},
        }},
        group = {{
            col = {{
                border_active = "{_hypr(c.primary)}",
                border_inactive = "{_hypr(c.border)}",
                border_locked_active = "{_hypr(c.secondary)}",
                border_locked_inactive = "{_hypr(c.border)}",
            }},
            groupbar = {{
                col = {{
                    active = "{_hypr(c.accent)}",
                    inactive = "{_hypr(c.border)}",
                    locked_active = "{_hypr(c.secondary)}",
                    locked_inactive = "{_hypr(c.border)}",
                }},
            }},
        }},
        decoration = {{
            rounding = {theme.shape.radius},
            active_opacity = {opacity:.2f},
            inactive_opacity = {max(0.2, opacity - 0.09):.2f},
            fullscreen_opacity = 1,
            blur = {{
                enabled = {"true" if theme.effects.blur > 0 else "false"},
                size = {theme.effects.blur},
                passes = {max(1, min(4, round(theme.effects.blur / 2)))},
                special = true,
            }},
        }},
    }})
end

return {{ apply = apply }}
"""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(destination)
    return destination


class ThemeManager:
    """Own the active GTK provider and safely hot-reload its TOML source."""

    def __init__(
        self,
        event_bus: EventBus,
        *,
        themes_dir: Path,
        structural_css_path: Path,
        active_name: str,
        hypr_export_path: Path | None = None,
        reload_hyprland: Callable[[], None] | None = None,
    ) -> None:
        self._event_bus = event_bus
        self._themes_dir = themes_dir
        self._structural_css_path = structural_css_path
        self._active_name = active_name
        self._hypr_export_path = hypr_export_path
        self._reload_hyprland = reload_hyprland or _reload_hyprland
        self._provider: Gtk.CssProvider | None = None
        self._monitor: Gio.FileMonitor | None = None
        self._reload_source_id = 0
        self._theme: Theme | None = None

    @property
    def theme(self) -> Theme:
        if self._theme is None:
            raise RuntimeError("theme manager has not started")
        return self._theme

    @property
    def available_themes(self) -> tuple[str, ...]:
        return tuple(discover_themes(self._themes_dir))

    def start(self) -> None:
        if not self.reload_current():
            raise RuntimeError(f"could not load active theme: {self._active_name}")
        self._watch_active_file()

    def set_theme(self, name: str) -> bool:
        if name not in discover_themes(self._themes_dir):
            return False
        previous = self._active_name
        self._active_name = name
        if not self.reload_current():
            self._active_name = previous
            return False
        self._watch_active_file()
        return True

    def reload_current(self) -> bool:
        catalog = discover_themes(self._themes_dir)
        path = catalog.get(self._active_name)
        if path is None:
            print(f"Jugoo theme: unknown theme {self._active_name!r}")
            return False
        try:
            theme = load_theme(path, key=self._active_name)
            structural_css = self._structural_css_path.read_text(encoding="utf-8")
            css = compile_gtk_css(theme, structural_css)
            provider = Gtk.CssProvider()
            provider.load_from_data(css.encode("utf-8"))
        except (OSError, UnicodeError, ValueError, GLib.Error) as error:
            print(f"Jugoo theme: reload rejected: {error}")
            return False

        screen = Gdk.Screen.get_default()
        if screen is None:
            print("Jugoo theme: no GDK screen available")
            return False
        Gtk.StyleContext.add_provider_for_screen(
            screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        previous_provider = self._provider
        self._provider = provider
        if previous_provider is not None:
            Gtk.StyleContext.remove_provider_for_screen(screen, previous_provider)

        global _active_theme
        self._theme = theme
        _active_theme = theme
        Gtk.StyleContext.reset_widgets(screen)
        self._export_hyprland(theme)
        self._event_bus.emit(THEME_CHANGED, theme)
        print(f"Jugoo theme: applied {theme.name}")
        return True

    def close(self) -> None:
        if self._reload_source_id:
            GLib.source_remove(self._reload_source_id)
            self._reload_source_id = 0
        if self._monitor is not None:
            self._monitor.cancel()
            self._monitor = None

    def _watch_active_file(self) -> None:
        if self._monitor is not None:
            self._monitor.cancel()
        path = self._themes_dir / f"{self._active_name}.toml"
        self._monitor = Gio.File.new_for_path(str(path)).monitor_file(
            Gio.FileMonitorFlags.NONE, None
        )
        self._monitor.connect("changed", self._on_theme_file_changed)

    def _on_theme_file_changed(self, *_args) -> None:
        if self._reload_source_id:
            GLib.source_remove(self._reload_source_id)
        self._reload_source_id = GLib.timeout_add(
            THEME_RELOAD_DEBOUNCE_MS, self._reload_after_debounce
        )

    def _reload_after_debounce(self) -> bool:
        self._reload_source_id = 0
        self.reload_current()
        return False

    def _export_hyprland(self, theme: Theme) -> None:
        if self._hypr_export_path is None:
            return
        try:
            export_hypr_theme(theme, self._hypr_export_path)
            self._reload_hyprland()
        except (OSError, subprocess.SubprocessError) as error:
            print(f"Jugoo theme: Hyprland export skipped: {error}")


def _section(payload: dict, name: str) -> dict:
    value = payload.get(name)
    if not isinstance(value, dict):
        raise ValueError(f"missing [{name}] section")
    return value


def _integer(section: dict, name: str, *, minimum: int, maximum: int) -> int:
    value = section.get(name)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _number(section: dict, name: str, *, minimum: float, maximum: float) -> float:
    value = section.get(name)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{name} must be a number")
    result = float(value)
    if not minimum <= result <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return result


def _rgb(color: str) -> tuple[int, int, int]:
    return tuple(int(color[index:index + 2], 16) for index in (1, 3, 5))  # type: ignore[return-value]


def _rgba(color: str, alpha: float) -> str:
    red, green, blue = _rgb(color)
    return f"rgba({red}, {green}, {blue}, {max(0.0, min(1.0, alpha)):.3f})"


def _hypr(color: str, alpha: int = 255) -> str:
    return f"rgba({color[1:].lower()}{alpha:02x})"


def _scale_radii(css: str, radius: int) -> str:
    scale = radius / 12.0

    def property_replacement(match: re.Match[str]) -> str:
        value = match.group(2)

        def pixel_replacement(pixel_match: re.Match[str]) -> str:
            original = float(pixel_match.group(1))
            if original >= 100:
                return pixel_match.group(0)
            scaled = 0 if original == 0 else max(1, round(original * scale))
            return f"{scaled}px"

        return match.group(1) + _PIXEL_VALUE.sub(pixel_replacement, value) + match.group(3)

    return _RADIUS_PROPERTY.sub(property_replacement, css)


def _reload_hyprland() -> None:
    subprocess.run(
        ["hyprctl", "reload"],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=3,
    )
