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
- `PopupOutsideDismiss` cierra con click en la barra o con `gapplication action com.jugoo.Shell dismiss-popups-outside` (Hyprland bind al *press*, non-consuming). No cierra por foco ni al sacar el puntero.
- `pointer_inside_widget()` para comprobar la posición real del puntero.

## Puertas (pickers centrados)

Search (menú/launcher), Clipboard y Emoji comparten [`PickerOverlay`](../../shell/widgets/pickers/overlay.py). La tarjeta hace un POP corto con escala y opacidad, con un pequeño rebote al abrir. El layout permanece fijo durante la animación; no se redimensiona ni se desplaza el contenido por frame.

La lógica vive en [`shell/ui/door.py`](../../shell/ui/door.py) (`BubblePop`).

Hyprland no debe animar estas layers (`layers` → `slide` las hace “caer” desde arriba). En `~/.config/hypr/config/windowrules.lua` hay `layer_rule` `jugoo-puertas-no-anim`.

## Salida desde la barra

Los popups anclados a un bloque de la barra son ventanas normales. Jugoo publica su sitio (centrado en el bloque si cabe, y por debajo de la barra) y Hyprland las coloca y anima la entrada y la salida (`jugoo_popup_place.lua`, `popin`). Las puertas centradas no entran aquí. Las notificaciones apiladas no son otra ventana: el mismo panel crece a la izquierda y muestra solo el grupo bajo el cursor.

- **Hover**: la lista padre (huecos incluidos) y el bloque de apiladas forman una sola región. El bloque se cierra al salir de esa región o al pasar por una notificación sin apiladas, nunca al cruzar el hueco entre tarjetas. No hay temporizadores.
- **Sin rebote**: Hyprland hace crecer desde el centro una ventana flotante cuando el cliente cambia de ancho. Jugoo la recoloca con el borde derecho fijo desde `configure-event`, justo cuando GTK fija el tamaño nuevo, y no desde `size-allocate`, que llega un fotograma tarde. Si Hyprland animara ese movimiento las notificaciones padre se desplazarían y volverían. Por eso, desde la primera apertura del bloque, el panel lleva `no_anim` (`set_popup_compositor_animations`). Se devuelve antes de cerrar para conservar el `popin` de salida.
- **Sin parpadeo**: GTK 3 publica el tamaño nuevo un fotograma antes que la imagen nueva. Durante ese fotograma, Hyprland estira o recorta la imagen vieja al marco nuevo. Por eso el ancho de la ventana cambia una sola vez al abrir y otra al cerrar (`_stack_revealer` sin transición). El movimiento visible es el bloque deslizándose dentro de un hueco que ya tiene su ancho final (`_stack_slide`). Al cerrar, el hueco se pliega cuando el bloque terminó de salir. Animar el ancho de la ventana fotograma a fotograma deformaba la lista padre en cerca del 40 % de los fotogramas.

## Dónde localizar cada popup

| Popup | Widget | Controller o dueño |
| --- | --- | --- |
| Centro de control | [`shell/widgets/centro_control/popup.py`](../../shell/widgets/centro_control/popup.py) | `control_center.py` |
| Multimedia | [`shell/widgets/multimedia/media_popup.py`](../../shell/widgets/multimedia/media_popup.py) | `media.py` |

El popup multimedia es un reproductor vertical (portada, transporte, loop, volumen).

- **Reproductor**: siempre Strawberry (sin fallback a otras fuentes). Si no corre, la barra/popup muestran estado vacío y play lo lanza.
- **Ventana**: fuentes no musicales (nunca Strawberry) + tarjeta con play/pausa; el cava sigue esa fuente.
- El bloque de la barra siempre abre el popup (también sin fuentes MPRIS).
| Audio por workspace | [`shell/widgets/espacios_trabajo/workspace_audio_popup.py`](../../shell/widgets/espacios_trabajo/workspace_audio_popup.py) | `workspace_interaction.py` |
| Panel de workspace | [`shell/widgets/espacios_trabajo/workspace_panel.py`](../../shell/widgets/espacios_trabajo/workspace_panel.py) | `workspace_interaction.py` |
| Notificaciones | [`shell/widgets/notificaciones/notification_popup.py`](../../shell/widgets/notificaciones/notification_popup.py) | `notifications.py` |
| OSD de volumen | [`shell/widgets/audio/volume_osd.py`](../../shell/widgets/audio/volume_osd.py) | `volume_osd.py` |
| Menú de energía | [`shell/widgets/barra/power.py`](../../shell/widgets/barra/power.py) | `PowerWidget` |

Cuando el comportamiento de cierre sea extraño, sigue primero el controller y después `PopupOutsideDismiss`; no empieces por CSS.