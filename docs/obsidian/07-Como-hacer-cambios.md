# Cómo hacer cambios

## Flujo recomendado

1. Define si el cambio es de fuente de datos, widget, controller, configuración o CSS.
2. Busca el símbolo exacto con `rg` antes de editar.
3. Cambia una sola responsabilidad por vez.
4. Ejecuta una prueba estrecha o compilación de Python.
5. Lanza el shell manualmente y prueba el comportamiento real.
6. Revisa el diff y anota decisiones importantes aquí si cambian la arquitectura.

## Ejemplo: añadir un dato al tooltip Radeon

1. Edita `GpuStats` en [`system.py`](../../shell/servicios/sistema/system.py).
2. Lee el nuevo sensor en `_read_gpu()`.
3. Añade una línea en `_gpu_tooltip()` de [`stats.py`](../../shell/widgets/barra/stats.py).
4. Comprueba el caso sensor ausente: todos los campos deben tolerar `None`.
5. Ejecuta las pruebas del servicio y una comprobación de sintaxis.

## Qué no hacer

- No leas `/sys` directamente desde el widget.
- No pongas comandos de sistema dentro del CSS.
- No confundas tooltip con `Gtk.Window`.
- No uses el hot spot para cambiar el estado del ventilador sin cambiar explícitamente el contrato térmico.
- No edites la raíz `style.css` esperando cambiar `shell/style.css`.

## Arranque manual

El entry point Python está en [`shell/__main__.py`](../../shell/__main__.py). El módulo se puede invocar desde la raíz del proyecto con:

```sh
python3 -m shell
```

La sesión debe tener GTK 3, PyGObject, Cairo, GTK Layer Shell y los servicios del escritorio disponibles.
