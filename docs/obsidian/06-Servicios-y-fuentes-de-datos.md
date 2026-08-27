# Servicios y fuentes de datos

| Servicio | Archivo | Fuente o integración |
| --- | --- | --- |
| Sistema | [`shell/servicios/sistema/system.py`](../../shell/servicios/sistema/system.py) | `/proc`, `/sys/class/hwmon`, `pci.ids` |
| Hyprland | [`shell/servicios/escritorio/hyprland.py`](../../shell/servicios/escritorio/hyprland.py) | IPC y comandos de Hyprland |
| Audio | [`shell/servicios/audio/audio.py`](../../shell/servicios/audio/audio.py) | PipeWire/WirePlumber mediante comandos o eventos |
| Visualizador | [`shell/servicios/audio/audio_visualizer.py`](../../shell/servicios/audio/audio_visualizer.py) | `pw-cat` y PCM |
| Red | [`shell/servicios/red/network.py`](../../shell/servicios/red/network.py) | NetworkManager |
| Multimedia | [`shell/servicios/multimedia/media.py`](../../shell/servicios/multimedia/media.py) | MPRIS por D-Bus |
| Arte multimedia | [`shell/servicios/multimedia/media_artwork.py`](../../shell/servicios/multimedia/media_artwork.py) | URI/cache de carátulas |
| Notificaciones | [`shell/servicios/notificaciones/notifications.py`](../../shell/servicios/notificaciones/notifications.py) | `org.freedesktop.Notifications` |
| Energía | [`shell/servicios/energia/power.py`](../../shell/servicios/energia/power.py) | comandos de sesión |
| Bandeja | [`shell/servicios/bandeja/tray.py`](../../shell/servicios/bandeja/tray.py) | D-Bus StatusNotifierItem |

## Regla para añadir una métrica

1. Añade el campo al dataclass del servicio.
2. Añade su descubrimiento/lectura en el mismo servicio.
3. Devuélvelo en el snapshot principal.
4. Pinta el dato en el widget.
5. Añade o reutiliza una clase CSS.
6. Agrega una prueba con fuente falsa o mock.