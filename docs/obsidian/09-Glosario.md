# Glosario

| Término | Significado en este proyecto |
| --- | --- |
| Shell | La aplicación GTK propia que dibuja la barra y sus ventanas auxiliares. |
| Widget | Componente GTK que muestra o recibe interacción. |
| Servicio | Componente que obtiene datos o ejecuta integración con el sistema. |
| Controller | Componente que coordina eventos, widgets y popups. |
| hwmon | Interfaz Linux de sensores de hardware en `/sys/class/hwmon`. |
| edge | Temperatura principal de la GPU AMD usada por el estado visual. |
| junction / hot spot | Punto térmico máximo de la GPU; se muestra, pero actualmente no gobierna el giro. |
| MPRIS | Interfaz D-Bus para reproductores multimedia. |
| GTK Layer Shell | Biblioteca que ancla ventanas GTK a bordes de Wayland. |
| EventBus | Bus interno para publicar y suscribir eventos del shell. |


En el proyecto las llamamos **puertas**.
| Nivel | Nombre |
|--------|--------|
| Nombre de familia (docs/código) | **puertas** |
| Clase base | `PickerOverlay` |
| Miembros | launcher/Search, clipboard, emoji |
| Tipo técnico (Wayland/Hyprland) | **layers** (wlr-layer-shell), no ventanas normales |

Por eso no las puedes mover: no pasan por el gestor de ventanas como un float/tiled. Están ancladas a bordes del monitor (`GtkLayerShell` en capa `OVERLAY`). Hyprland las ve como layers (`hyprctl layers`), con namespaces:

- `shell-app-launcher`
- `shell-clipboard-picker`
- `shell-emoji-picker`

Las ventanas normales (incluso popups flotantes de la barra) sí tienen `windowrule` y se pueden mover; las puertas usan `layer_rule`.