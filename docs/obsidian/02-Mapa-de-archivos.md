# Mapa de archivos

## Archivos raíz

| Ruta | Responsabilidad |
| --- | --- |
| [`shell/`](../../shell/) | Aplicación GTK propia. |

## Shell

| Ruta | Buscar aquí cuando... |
| --- | --- |
| [`shell/app.py`](../../shell/app.py) | Necesitas montar, quitar u ordenar módulos, servicios o controllers. |
| [`shell/config.py`](../../shell/config.py) | Necesitas cambiar tamaños, intervalos, offsets o límites editables. |
| [`shell/style.css`](../../shell/style.css) | Necesitas colores, clases, radios, padding o estados visuales. |
| [`shell/layout.py`](../../shell/layout.py) | Necesitas cambiar las zonas de la barra. |
| [`shell/window_identity.py`](../../shell/window_identity.py) | Necesitas títulos, clases, identidad o reglas de ventanas GTK. |
| [`shell/popup_handle.py`](../../shell/popup_handle.py) | Necesitas fade, referencias lazy o cierre por puntero fuera. |
| [`shell/ui/`](../../shell/ui/) | Necesitas primitivas visuales y tokens compartidos. |
| [`shell/widgets/barra/`](../../shell/widgets/barra/) | Necesitas modificar un módulo visible de la barra. |
| [`shell/widgets/centro_control/`](../../shell/widgets/centro_control/) | Necesitas modificar el centro de control y sus secciones. |
| [`shell/widgets/espacios_trabajo/`](../../shell/widgets/espacios_trabajo/) | Necesitas paneles o popups de workspaces. |
| [`shell/widgets/multimedia/`](../../shell/widgets/multimedia/) | Necesitas popup, formato o espectro multimedia. |
| [`shell/widgets/notificaciones/`](../../shell/widgets/notificaciones/) | Necesitas historial, popup, toast o icono de notificaciones. |
| [`shell/servicios/`](../../shell/servicios/) | Necesitas cambiar de dónde se obtiene un dato o cómo se ejecuta una acción. |
| [`shell/controllers/`](../../shell/controllers/) | Necesitas cambiar qué ocurre al hacer click, hover, scroll o cerrar una vista. |
| [`shell/tests/`](../../shell/tests/) | Necesitas ver contratos existentes o añadir una verificación. |

## Atajo por síntoma

- “No aparece el dato”: servicio y fuente del sistema.
- “El dato es incorrecto”: parser o selección de sensor en servicio.
- “El dato está bien pero se ve mal”: widget y `shell/style.css`.
- “El popup se abre o cierra mal”: controller, widget popup y `popup_handle.py`.
- “El módulo no aparece”: `shell/app.py` y `shell/layout.py`.
