# Pruebas y verificación

## Prueba rápida de sintaxis

Desde la raíz del proyecto:

```sh
python3 -m compileall -q shell
```

## Pruebas existentes

Las verificaciones están en [`shell/tests/`](../../shell/tests/). Hay cobertura para audio, red, hotspot, notificaciones, energía, bandeja, popups y OSD de volumen.

Para ejecutar una prueba directa que no requiera pytest:

```sh
python3 shell/tests/test_power_safe.py
```

Para una colección con pytest, si está instalado:

```sh
python3 -m pytest shell/tests
```

## Antes y después de tocar GPU

- Verifica que `amdgpu` aparece en `/sys/class/hwmon/*/name`.
- Comprueba que `edge` existe y que su `*_input` cambia al cargar la GPU.
- Confirma que el tooltip soporta sensores ausentes.
- Comprueba que el color cambia en 49/50/72/73 °C según el contrato actual.
- Verifica que cerrar el shell elimina el timer de `StatsWidget`.

## Señales de un fallo

- `-- °C`: sensor no descubierto o lectura inválida.
- Tooltip `GPU: sin datos`: no existe temperatura `edge`.
- Icono quieto: temperatura por debajo de `GPU_FAN_START_TEMP` o dato ausente.
- Icono con color inesperado: revisa `GPU_TEMP_RANGES` y las clases en `shell/style.css`.
