# Configuración editable

## Configuración oficial

[`shell/config.py`](../../shell/config.py) contiene la configuración editable del shell GTK. La implementación clásica de Waybar fue retirada para mantener una única barra.

## Valores útiles del shell

| Constante | Efecto |
| --- | --- |
| `SYSTEM_STATS_UPDATE_INTERVAL` | Frecuencia de lectura de CPU, RAM y GPU. |
| `STATS_GPU_FAN_ICON_SIZE` | Tamaño del icono Radeon. |
| `GPU_FAN_START_TEMP` | Temperatura desde la que el icono gira. Está en `system.py`. |
| `GPU_TEMP_RANGES` | Límites de color térmico. Está en `system.py`. |
| `POPUP_OUTSIDE_DISMISS_GRACE_MS` | Tiempo antes de cerrar popups al salir con el puntero. |
| `CONTROL_CENTER_POPUP_WIDTH` | Ancho del centro de control. |
| `MEDIA_POPUP_WIDTH` | Ancho del popup multimedia. |
| `NOTIFICATION_POPUP_WIDTH` | Ancho del popup de notificaciones. |

## CSS

La apariencia se define en [`themes/space.toml`](../../themes/space.toml).
`[colors]` contiene roles semánticos; `[effects]`, `[shape]` y `[animation]`
controlan opacidad/blur, radios y fades.

[`shell/style.css`](../../shell/style.css) solo contiene selectores y estructura.
No agregues colores literales allí ni en widgets. Para crear otro tema, copia
`space.toml`, cambia sus valores y selecciona el nombre con `ACTIVE_THEME` en
[`shell/config.py`](../../shell/config.py).

El archivo activo se vigila y recarga al guardarlo. También se puede forzar:

```sh
jugoo --reload-theme
```

Una recarga válida regenera
`~/.config/hypr/config/jugoo_theme_generated.lua` y ejecuta `hyprctl reload`.
La línea que aplica ese archivo debe permanecer al final de
`~/.config/hypr/hyprland.lua`, después de Noctalia.


## Fuentes externas

El código lee fuentes del sistema, no guarda esos valores en un archivo del proyecto: `/proc/stat`, `/proc/meminfo`, `/sys/class/hwmon` y `/usr/share/hwdata/pci.ids` o `/usr/share/misc/pci.ids`.
