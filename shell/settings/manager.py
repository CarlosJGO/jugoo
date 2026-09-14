"""Runtime settings: persist, patch shell.config, apply live hooks, emit events."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from gi.repository import GLib

from .. import config as shell_config
from ..eventbus import EventBus
from .layout_model import default_layout_json
from .night_mode import NightModeService, NightModeStatus
from .schema import (
    APPLY_LIVE,
    APPLY_RELOAD,
    APPLY_RESTART,
    CategoryId,
    SettingDef,
    build_settings_catalog,
    settings_for_category,
)
from .store import SettingsStore, default_settings_path

SETTINGS_CHANGED = "settings_changed"
NIGHT_MODE_CHANGED = "night_mode_changed"

ApplyHook = Callable[["SettingsManager", SettingDef, Any], None]


class SettingsManager:
    """Single source of user preferences consumed by the Settings Center UI."""

    def __init__(
        self,
        event_bus: EventBus,
        *,
        path: Path | None = None,
        theme_setter: Callable[[str], bool] | None = None,
        theme_choices: Callable[[], tuple[tuple[str, str], ...]] | None = None,
        font_setter: Callable[[str], bool] | None = None,
        font_choices: Callable[[], tuple[tuple[str, str], ...]] | None = None,
    ) -> None:
        catalog = build_settings_catalog()
        # Inject default layout JSON so the key is never empty in a fresh store.
        patched: list[SettingDef] = []
        for item in catalog:
            if item.key == "layout.modules_json" and not item.default:
                patched.append(
                    SettingDef(
                        key=item.key,
                        category=item.category,
                        label=item.label,
                        description=item.description,
                        value_type=item.value_type,
                        default=default_layout_json(),
                        config_attr=item.config_attr,
                        apply=item.apply,
                        minimum=item.minimum,
                        maximum=item.maximum,
                        step=item.step,
                        choices=item.choices,
                        tier=item.tier,
                        section=item.section,
                        unit=item.unit,
                    )
                )
            else:
                patched.append(item)
        self._store = SettingsStore(path or default_settings_path(), catalog=tuple(patched))
        self._event_bus = event_bus
        self._theme_setter = theme_setter
        self._theme_choices = theme_choices
        self._font_setter = font_setter
        self._font_choices = font_choices
        self._hooks: dict[str, ApplyHook] = {}
        self._volume_osd_delay_setter: Callable[[int], None] | None = None
        self._workspace_hover_setter: Callable[[int], None] | None = None
        self._night = NightModeService(on_status=self._emit_night_status)
        self._schedule_source_id = 0
        self._register_builtin_hooks()

    @property
    def store(self) -> SettingsStore:
        return self._store

    @property
    def night_mode(self) -> NightModeService:
        return self._night

    @property
    def path(self) -> Path:
        return self._store.path

    def start(self) -> None:
        self._store.load()
        self._sync_all_config_attrs()
        self._apply_all(initial=True)
        self._ensure_schedule_timer()

    def close(self) -> None:
        if self._schedule_source_id:
            GLib.source_remove(self._schedule_source_id)
            self._schedule_source_id = 0
        self._night.close()

    def catalog(self) -> tuple[SettingDef, ...]:
        return self._store.catalog

    def categories_present(self) -> tuple[CategoryId, ...]:
        seen: list[CategoryId] = []
        for item in self._store.catalog:
            if item.category not in seen:
                seen.append(item.category)
        return tuple(seen)

    def settings_for(self, category: CategoryId) -> tuple[SettingDef, ...]:
        return settings_for_category(category, self._store.catalog)

    def get(self, key: str) -> Any:
        return self._store.get(key)

    def definition(self, key: str) -> SettingDef:
        return self._store.definition(key)

    def choices_for(self, key: str) -> tuple[tuple[str, str], ...]:
        definition = self._store.definition(key)
        if key == "tema.active" and self._theme_choices is not None:
            return self._theme_choices()
        if key == "apariencia.ui_font" and self._font_choices is not None:
            from ..ui.fonts import SYSTEM_UI_FONT_CHOICE, normalize_ui_font

            choices = self._font_choices()
            current = normalize_ui_font(self._store.get(key))
            if current and current not in {item for item, _label in choices}:
                return choices + ((current, f"{current} (no instalada)"),)
            # Ensure the system sentinel is present even if a custom provider omits it.
            if not any(item == SYSTEM_UI_FONT_CHOICE for item, _label in choices):
                choices = ((SYSTEM_UI_FONT_CHOICE, "Sistema (predeterminada)"),) + choices
            return choices
        return definition.choices

    def set_volume_osd_delay_hook(self, callback: Callable[[int], None]) -> None:
        self._volume_osd_delay_setter = callback

    def set_workspace_hover_hook(self, callback: Callable[[int], None]) -> None:
        self._workspace_hover_setter = callback

    def register_hook(self, key: str, hook: ApplyHook) -> None:
        self._hooks[key] = hook

    def set(self, key: str, value: Any, *, persist: bool = True) -> bool:
        changed = self._store.set(key, value)
        if not changed:
            return False
        definition = self._store.definition(key)
        self._patch_config(definition, self._store.get(key))
        self._run_hook(definition, self._store.get(key))
        if persist:
            try:
                self._store.save()
            except OSError as error:
                print(f"Jugoo settings: save failed: {error}")
        self._event_bus.emit(
            SETTINGS_CHANGED,
            {
                "key": key,
                "value": self._store.get(key),
                "apply": definition.apply,
            },
        )
        if key.startswith("modo_noche."):
            self._apply_night_mode()
            self._ensure_schedule_timer()
        return True

    def apply_mode_label(self, mode: str) -> str:
        if mode == APPLY_LIVE:
            return "Se aplica al instante"
        if mode == APPLY_RELOAD:
            return "Requiere recargar Jugoo"
        if mode == APPLY_RESTART:
            return "Requiere reiniciar Jugoo / vigilante"
        return mode

    def _register_builtin_hooks(self) -> None:
        self._hooks["tema.active"] = self._apply_theme
        self._hooks["apariencia.ui_font"] = self._apply_ui_font
        self._hooks["popups.volume_osd_hide_ms"] = self._apply_volume_osd
        self._hooks["comportamiento.workspace_hover_delay_ms"] = self._apply_workspace_hover

    def _apply_all(self, *, initial: bool) -> None:
        for definition in self._store.catalog:
            value = self._store.get(definition.key)
            if definition.key.startswith("modo_noche."):
                continue
            if initial and definition.apply != APPLY_LIVE and definition.key != "tema.active":
                # Non-live knobs already patched into config for next consumers.
                continue
            self._run_hook(definition, value)
        self._apply_night_mode()

    def _sync_all_config_attrs(self) -> None:
        for definition in self._store.catalog:
            self._patch_config(definition, self._store.get(definition.key))

    def _patch_config(self, definition: SettingDef, value: Any) -> None:
        if not definition.config_attr:
            return
        setattr(shell_config, definition.config_attr, value)
        if definition.config_attr == "AUDIO_VISUALIZER_FPS":
            fps = max(1, int(value))
            setattr(shell_config, "AUDIO_VISUALIZER_INTERVAL_MS", 1000 // fps)

    def _run_hook(self, definition: SettingDef, value: Any) -> None:
        hook = self._hooks.get(definition.key)
        if hook is not None:
            hook(self, definition, value)

    def _apply_theme(self, _manager: SettingsManager, _definition: SettingDef, value: Any) -> None:
        if self._theme_setter is None:
            return
        name = str(value)
        if not self._theme_setter(name):
            print(f"Jugoo settings: theme {name!r} rejected")

    def _apply_ui_font(
        self, _manager: SettingsManager, _definition: SettingDef, value: Any
    ) -> None:
        if self._font_setter is None:
            return
        from ..ui.fonts import normalize_ui_font

        if not self._font_setter(normalize_ui_font(value)):
            print(f"Jugoo settings: ui font {value!r} rejected")

    def _apply_volume_osd(
        self, _manager: SettingsManager, _definition: SettingDef, value: Any
    ) -> None:
        if self._volume_osd_delay_setter is not None:
            self._volume_osd_delay_setter(int(value))

    def _apply_workspace_hover(
        self, _manager: SettingsManager, _definition: SettingDef, value: Any
    ) -> None:
        if self._workspace_hover_setter is not None:
            self._workspace_hover_setter(int(value))

    def _apply_night_mode(self) -> NightModeStatus:
        return self._night.configure(
            enabled=bool(self._store.get("modo_noche.enabled")),
            temperature=int(self._store.get("modo_noche.temperature")),
            auto_schedule=bool(self._store.get("modo_noche.auto_schedule")),
            start_hour=int(self._store.get("modo_noche.start_hour")),
            end_hour=int(self._store.get("modo_noche.end_hour")),
        )

    def _emit_night_status(self, status: NightModeStatus) -> None:
        self._event_bus.emit(NIGHT_MODE_CHANGED, status)

    def _ensure_schedule_timer(self) -> None:
        auto = bool(self._store.get("modo_noche.auto_schedule")) and bool(
            self._store.get("modo_noche.enabled")
        )
        if auto and not self._schedule_source_id:
            self._schedule_source_id = GLib.timeout_add_seconds(60, self._on_schedule_tick)
        elif not auto and self._schedule_source_id:
            GLib.source_remove(self._schedule_source_id)
            self._schedule_source_id = 0

    def _on_schedule_tick(self) -> bool:
        if not bool(self._store.get("modo_noche.auto_schedule")):
            self._schedule_source_id = 0
            return False
        self._apply_night_mode()
        return True
