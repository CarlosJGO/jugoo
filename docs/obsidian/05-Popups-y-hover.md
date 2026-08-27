# Popups y hover

## No todos los tooltips son popups

- Tooltip GTK: texto nativo asignado con `set_tooltip_text`, como el de la Radeon.
- Popup GTK: una clase `Gtk.Window` separada, normalmente en `shell/widgets/**`.
- Controller: decide cuándo abrir o cerrar el popup.
- `PopupHandle`: crea ventanas bajo demanda y limpia la referencia cuando reciben `destroy`.

## Utilidades comunes

[`shell/popup_handle.py`](../../shell/popup_handle.py) contiene:

- `present_popup()` y `hide_popup()` para fades de opacidad.
- `PopupHandle` para creación lazy.
- `PopupOutsideDismiss` para cierre al salir, clicks en la barra y cambios de ventana activa.
- `pointer_inside_widget()` para comprobar la posición real del puntero.

## Dónde localizar cada popup

| Popup | Widget | Controller o dueño |
| --- | --- | --- |
| Centro de control | [`shell/widgets/centro_control/popup.py`](../../shell/widgets/centro_control/popup.py) | `control_center.py` |
| Multimedia | [`shell/widgets/multimedia/media_popup.py`](../../shell/widgets/multimedia/media_popup.py) | `media.py` |
| Audio por workspace | [`shell/widgets/espacios_trabajo/workspace_audio_popup.py`](../../shell/widgets/espacios_trabajo/workspace_audio_popup.py) | `workspace_interaction.py` |
| Panel de workspace | [`shell/widgets/espacios_trabajo/workspace_panel.py`](../../shell/widgets/espacios_trabajo/workspace_panel.py) | `workspace_interaction.py` |
| Notificaciones | [`shell/widgets/notificaciones/notification_popup.py`](../../shell/widgets/notificaciones/notification_popup.py) | `notifications.py` |
| OSD de volumen | [`shell/widgets/audio/volume_osd.py`](../../shell/widgets/audio/volume_osd.py) | `volume_osd.py` |
| Menú de energía | [`shell/widgets/barra/power.py`](../../shell/widgets/barra/power.py) | `PowerWidget` |

Cuando el comportamiento de cierre sea extraño, sigue primero el controller y después `PopupOutsideDismiss`; no empieces por CSS.