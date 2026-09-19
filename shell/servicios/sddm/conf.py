"""Generate SDDM ``theme.conf`` from Jugoo settings + active Theme colors."""

from __future__ import annotations

from dataclasses import dataclass

from ...ui.theme import Theme


@dataclass(frozen=True)
class SddmThemeOptions:
    background: str
    background_dim: float
    background_fill: str  # crop | fit
    show_avatar: bool
    show_clock: bool
    form_opacity: float
    form_radius: int


def render_theme_conf(theme: Theme, options: SddmThemeOptions) -> str:
    """Return ``theme.conf`` text consumed by the Jugoo SDDM QML greeter."""
    dim = max(0.0, min(1.0, float(options.background_dim)))
    opacity = max(0.2, min(1.0, float(options.form_opacity)))
    radius = max(0, min(48, int(options.form_radius)))
    fill = options.background_fill if options.background_fill in {"crop", "fit"} else "crop"
    colors = theme.colors

    lines = [
        "[General]",
        f"background={options.background}",
        "defaultBackground=",
        f"backgroundFill={fill}",
        f"dimBackground={dim:.2f}",
        f"dimColor={colors.background}",
        f"showAvatar={'true' if options.show_avatar else 'false'}",
        f"showClock={'true' if options.show_clock else 'false'}",
        f"formOpacity={opacity:.2f}",
        f"formRadius={radius}",
        f"backgroundColor={colors.background}",
        f"surfaceColor={colors.surface}",
        f"surfaceAltColor={colors.surface_alt}",
        f"primaryColor={colors.primary}",
        f"secondaryColor={colors.secondary}",
        f"accentColor={colors.accent}",
        f"textColor={colors.text}",
        f"textMutedColor={colors.text_muted}",
        f"borderColor={colors.border}",
        f"errorColor={colors.error}",
        "",
    ]
    return "\n".join(lines)
