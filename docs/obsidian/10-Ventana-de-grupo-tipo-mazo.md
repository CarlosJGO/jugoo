# Ventana de grupo tipo mazo (tarea 6.1)

Investigación y prototipo para que las notificaciones agrupadas vivan en una
ventana propia que sale de detrás del popup padre, como una carta de un mazo.
El padre no se mueve ni cambia de tamaño.

## Por qué una ventana aparte

- Cambiar el ancho de una ventana GTK3 en Hyprland siempre deja un fotograma
  viejo: GTK publica las nuevas pistas de tamaño un fotograma antes que el
  nuevo búfer y Hyprland estira o recorta el búfer anterior.
- Mover una ventana no cambia su búfer: es trabajo del compositor y no deja
  fotogramas deformados. La carta se mueve; nadie cambia de tamaño.

## Entorno verificado

- Hyprland 0.56.2, configuración en Lua (`hl.*`).
- Equivalentes de la sintaxis clásica:
  - `windowrulev2` → `hl.window_rule({ name, match = { title = ... }, ... })`
  - `noanim` → `no_anim = true`; `nofocus` → `no_focus = true`;
    `noshadow` → `no_shadow = true`; `noborder` → `border_size = 0`;
    `float` → `float = true`
  - `animation` (regla) → `animation = "<estilo>"`; global con `hl.animation`
  - `pin` → `hl.dsp.window.pin`; `resizewindowpixel` → `hl.dsp.window.resize`
  - `movewindowpixel exact` → `hl.dsp.window.move({ window, x, y, relative = false })`
  - `alterzorder` → `hl.dsp.window.alter_zorder({ window, mode = "top" | "bottom" })`
  - `--batch` → `hyprctl eval '<lua>'` con varios `hl.dispatch(...)`: se ejecutan
    en la misma vuelta del bucle de Hyprland, sin fotograma intermedio.
- Reglas en tiempo de ejecución: `hyprctl eval` puede registrarlas y
  `rule:set_enabled(false)` las apaga.

## Trampas encontradas

- **Mover sube la ventana.** Hyprland pone encima una ventana flotante al
  moverla. Un `alter_zorder` en un mensaje aparte deja al menos un fotograma con
  la carta tapando al padre.
- **`mode = "bottom"` la hunde demasiado.** Queda debajo de todas las ventanas
  del espacio de trabajo y una ventana en mosaico la tapa por completo.
- **Orden correcto:** en un solo `eval`, mover la carta, subir la carta y
  después subir el padre. La carta queda justo debajo del padre y encima del
  resto.
- **Opacidad del tema.** Con la regla `opacity = "1.0 override 1.0 override"`
  el padre se pinta al ~0,96 y la carta escondida se transparenta (255,0,0 se
  ve como 244,9,0). La secuencia `set_prop` `opacity_override = 1`,
  `opacity_inactive_override = 1`, `opacity = 1` deja el padre opaco del todo.
- **Reglas con nombre.** Volver a registrar una regla con el mismo nombre
  conserva campos de la versión anterior: hay que dar cada campo de forma
  explícita (por ejemplo `no_anim = false`).
- **Primer fotograma.** La carta se mapea velada (no pinta nada), se coloca
  dentro del padre y después se desvela; nunca aparece por encima.

## Reglas de la ventana secundaria

- `float = true`, `no_focus = true`, `border_size = 0`, `no_shadow = true`,
  `no_anim = true`: el borde o la sombra se verían alrededor del padre
  mientras la carta está escondida, y Jugoo es dueño del movimiento.
- `windowsIn` no se ve: la carta se mapea velada y ya escondida. `windowsOut`
  tampoco: se cierra solo estando escondida. `windowsMove` no actúa por
  `no_anim`.
- El padre se fuerza opaco con `set_prop` (ver trampas).

## Apilamiento

- Cada `move` de la carta va en el mismo `eval` que «carta arriba, padre
  arriba». Se reaplica en el mapeo y en cada paso del deslizamiento.
- Si otra ventana flotante aparece encima, queda encima de las dos; la pareja
  conserva su orden relativo.
- Si el padre gana el foco, Hyprland lo sube: sigue encima de la carta. La
  carta no toma foco (`no_focus`), así que nunca se sube sola.

## Geometría

- El padre no se mueve ni cambia de tamaño.
- **Carta = padre menos `NOTIFICATION_DECK_INSET` (6 px) por lado.** Con el
  rectángulo escondido dentro del padre, ningún borde asoma (cubre también
  redondeos de escala fraccional). Las filas que no caben se desplazan dentro.
- La carta no depende de la fila que la abre: cambiar de fila no la
  redimensiona. Solo cambia de tamaño si cambia el padre, y entonces se vuelve
  a mapear velada (un redimensionado mapeado se estiraría un fotograma).
- **Rectángulo visible:** al lado del padre con `NOTIFICATION_DECK_OVERLAP`
  (18 px) aún debajo de él; el contenido deja libre esa franja.
- El movimiento es solo horizontal: misma `y` escondida y visible.

## Límites de pantalla

- Se usa el monitor del padre en coordenadas lógicas de Hyprland
  (`j/monitors`: `x`, `y`, `width / scale`, `height / scale`).
- Si a la izquierda no cabe la carta entera, sale por la derecha. Si no cabe
  por ningún lado, sale por el lado con más sitio y queda recortada por el
  borde del monitor; nunca se encoge durante la animación.

## Oclusión en reposo

- Medida en el prototipo: 0 fotogramas con la carta asomando, con el padre
  opaco y el rectángulo escondido dentro de él. Tolerancia: 0 px.

## Hover entre ventanas

- Con `resize_on_border` (área de 10 px) el puntero cruza una franja que no es
  de ninguna superficie al pasar del padre a la carta. Medido (6.4): la salida
  del padre llegaba a x≈1130 y la entrada de la carta a x≈868, demasiado tarde
  para cualquier espera razonable. Los eventos de cruce de GTK no sirven aquí.
- Por eso el popup y la carta forman una región lógica: mientras el popup está
  abierto, `NotificationDeck` lee la posición del cursor de Hyprland
  (`j/cursorpos`) cada `NOTIFICATIONS_HOVER_POLL_MS` (40 ms) y la compara con
  la unión del popup y la parte visible de la carta (`HoverRegion`).
- Entrar cuenta al instante; salir, solo si dura
  `NOTIFICATIONS_DECK_HOVER_GRACE_MS` (120 ms), el único retardo. Un botón
  pulsado sobre la carta (arrastre para descartar) mantiene la región.
- Al entrar se pausa la expiración de notificaciones y al salir se reanuda con
  el tiempo que le quedaba a cada una.

## Señal de fin del deslizamiento

- Hyprland no tiene evento de fin de movimiento (`hl.on` no lo ofrece), la
  ventana Lua no expone la posición animada y `at` de `j/clients` salta al
  destino en cuanto se envía el `move`.
- Con B el fin era el último paso: Jugoo enviaba la posición final y la
  respuesta del socket la confirmaba.
- Con A (actual) el fin se da por tiempo: `NOTIFICATIONS_DECK_DURATION_MS`
  (500 ms) tiene que coincidir con `windowsMove` (velocidad 5 × 100 ms).

## Mecanismos comparados

- **A — un `move` animado por `windowsMove` (actual).** Hyprland hace toda la
  animación. Usa la curva global (`smoothOut`, velocidad 5): en el primer
  fotograma la carta ya asoma 319 de 348 px y luego se arrastra ~40
  fotogramas (~400 ms) de 1 en 1 px.
- **B — un `move` instantáneo por fotograma GTK, con `no_anim` (primera versión).** Curva y
  duración en Jugoo (220 ms, coseno simétrico). Primer fotograma 23 px, pasos
  de ≤45 px, asentado en ~22 fotogramas.
- **C — superficie fija más grande.** Descartado: es la ventana-anfitrión
  transparente que ya se rechazó y no evita el cambio de tamaño.

## Resultados del prototipo

`shell/prototypes/notification_deck.py`, 5 ciclos por modo, repetido varias
veces, monitor de 100 Hz:

| Medida | A | B |
|---|---|---|
| Estados de geometría del padre (`j/clients` cada 1 ms) | 1 | 1 |
| Fotogramas con el padre dañado | 0 | 0 |
| Fotogramas en reposo con la carta asomando | 0 | 0 |
| Fotogramas con la carta encima al mapear | 0 | 0 |
| Retrocesos durante el deslizamiento | 0 | 0 |
| Primer fotograma visible (px fuera del padre) | 319 | 23 |
| Fotogramas hasta asentarse | 40 | 22 |

## Máquina de estados

Implementada en `shell/widgets/notificaciones/notification_deck_motion.py`
(`DeckMachine`, sin GTK).

| Estado | Entra con | Sale con |
|---|---|---|
| `HIDDEN` | mapeo velado; fin de `EXITING` | petición: si el grupo ya está cargado → `ENTERING`; si no, se prepara (`loading`) y `loaded()` → `ENTERING` |
| `ENTERING` | contenido pintado en su sitio | fin de la duración → `VISIBLE`; otro grupo o ninguno → `EXITING` desde la posición actual |
| `VISIBLE` | fin de `ENTERING` | otro grupo o ninguno → `EXITING` |
| `EXITING` | otro grupo o ninguno | fin de la duración → `HIDDEN` (y prepara la petición pendiente); vuelve el mismo grupo → `ENTERING` desde donde está |

- Solo cuenta la petición más reciente; un `loaded()` de una petición vieja se
  ignora.
- Contenido, tamaño y posición escondida solo cambian en `HIDDEN`.
- Cada salida o vuelta es una sola orden `move` animada; Hyprland la desliza.
- Una inversión a mitad de camino tarda la duración entera, como Hyprland.
- Movimiento reducido: un `move` sin animación a la posición final.
- Con la carta fuera, el grupo siguiente se construye aparte (`prepare`) y se
  intercambia de una vez (`fill`) al quedar escondida.
- Mientras se desliza, la región de hover cubre todo el recorrido.

## Reutilizable

- `notification_grouping.py` (claves y agrupación) tal cual.
- `notification_mini_row.py` y la región de hover del popup actual.
- De `notification_group_window.py`: el paginador por bloques y la medida de
  alturas; su colocación propia se sustituye por la geometría del mazo.

## Decisión

**Primero B, ahora A.** La primera versión movía la carta un paso por fotograma GTK con
`no_anim`, cada paso en un `eval` con el reordenamiento. Fin confirmado por la
posición final, duración y curva configurables. A se descartó porque Hyprland
no informa del fin de la animación y su curva es global; C, por ser la
ventana-anfitrión transparente ya rechazada.

En el Jugoo real, las paradas del hilo principal (~150 ms) hacían saltar la
carta con B. Se pasó a A: la regla deja `no_anim = true` y
`move_window_under(..., animate=True)` cambia la propiedad `no_anim` a 0 en el
mismo `eval` que el `move` (con `animate=False` la pone a 1, para mapear y
hundir sin animación). No sirve volver a poner `no_anim` en el mismo `eval`
después del `move`: se aplica antes de que empiece la animación y la anula. El
fin se da por tiempo (ver arriba).
