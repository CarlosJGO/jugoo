# Waybar Shell: mapa de trabajo

Esta carpeta es un vault pequeño para entender y modificar el proyecto sin depender de memoria externa. Las notas usan enlaces de Obsidian (`[[...]]`) y enlaces Markdown a archivos reales.

## Ruta recomendada

1. [[01-Arquitectura]] para entender el arranque y las capas.
2. [[02-Mapa-de-archivos]] para localizar cada responsabilidad.
3. [[03-Monitor-GPU-Radeon]] para el caso del ventilador y su tooltip.
4. [[04-Configuracion-editable]] para saber qué valores cambiar primero.
5. [[05-Popups-y-hover]] para ventanas flotantes y cierres por puntero.
6. [[06-Servicios-y-fuentes-de-datos]] para saber de dónde sale cada dato.
7. [[07-Como-hacer-cambios]] para trabajar con una secuencia segura.
8. [[08-Pruebas-y-verificacion]] para comprobar cambios manuales.

## Resumen del estado

- El proyecto es un shell GTK 3 propio que se dibuja como barra superior mediante GTK Layer Shell.
- El shell propio arranca desde `shell/__main__.py` y monta sus widgets desde `shell/app.py`.
- El icono de la Radeon y su tooltip están en `shell/widgets/barra/stats.py`.
- Las lecturas Radeon vienen de `/sys/class/hwmon` a través de `shell/servicios/sistema/system.py`.
- No hay documentación de proyecto ni archivos de servicio visibles en esta carpeta; el modo de lanzamiento depende de cómo se invoque `python3 -m shell` o de la configuración externa del escritorio.

## Pregunta concreta: ¿dónde está el popup de la Radeon?

No es un popup GTK independiente. Es un `Gtk.DrawingArea` llamado `FanIcon`, dentro de `StatsWidget`. El texto que se muestra al dejar el cursor encima se asigna aquí:

[`shell/widgets/barra/stats.py`](../../shell/widgets/barra/stats.py#L296-L305)

La lectura se resuelve aquí:

[`shell/servicios/sistema/system.py`](../../shell/servicios/sistema/system.py#L172-L218)

Para cambiar el texto del tooltip, edita `_gpu_tooltip()`. Para añadir otro dato, primero añádelo a `GpuStats`, luego léelo en `_read_gpu()` y finalmente inclúyelo en `_gpu_tooltip()`.

## Regla mental

**Servicio = obtiene datos. Widget = presenta datos. Config = ajusta constantes. CSS = apariencia. Controller = coordina interacción.**
