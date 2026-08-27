# Monitor GPU Radeon

## Ruta completa

```text
/sys/class/hwmon/<dispositivo amdgpu>/*_input
        -> shell/servicios/sistema/system.py
        -> GpuStats
        -> shell/widgets/barra/stats.py
        -> FanIcon + tooltip GTK
        -> shell/style.css
```

## Qué hace cada archivo

- [`system.py`](../../shell/servicios/sistema/system.py): busca el hwmon cuyo `name` es `amdgpu`, elige `edge`, `junction` y `mem`, lee temperaturas en miligrados y obtiene la VRAM desde `/sys/class/drm/card*/device/mem_info_vram_*`.
- [`stats.py`](../../shell/widgets/barra/stats.py): dibuja el ventilador con Cairo, decide si gira y compone el tooltip.
- [`shell/config.py`](../../shell/config.py): controla intervalo de actualización y tamaño del icono.
- [`shell/style.css`](../../shell/style.css): define `.stats-gpu-fan`, `.stats-gpu-fan-spinning` y los colores térmicos.

## Umbrales actuales

En `system.py`:

- `GPU_TEMP_RANGES`: hasta 49 °C `cold`, hasta 72 °C `normal`, desde 73 °C `hot`.
- `GPU_FAN_START_TEMP`: desde 50 °C se anima el ventilador.
- El hot spot no decide el color ni el giro; se muestra como dato adicional del tooltip.

## Cambiar el tooltip

Edita `StatsWidget._gpu_tooltip()` en [`stats.py`](../../shell/widgets/barra/stats.py#L307-L318). El formato actual es:

```text
<nombre>
Temperatura: <edge> °C
Hot spot: <junction> °C   # solo si existe
```

El tooltip muestra también `vram_used_bytes` y `vram_total_bytes` convertidos a GiB. Para añadir otro dato, primero amplía `GpuStats` y la fuente de lectura.

## Diagnóstico manual

```sh
for device in /sys/class/hwmon/hwmon*; do
    printf '%s: ' "$device"
    cat "$device/name" 2>/dev/null
done

grep -H . /sys/class/hwmon/hwmon*/temp*_label /sys/class/hwmon/hwmon*/temp*_input 2>/dev/null
```

Los valores `*_input` están en miligrados Celsius. Si el driver no expone `amdgpu`, el widget queda en `-- °C` y el tooltip indica `sin datos`.