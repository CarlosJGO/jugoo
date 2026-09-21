# Jugoo

> [!abstract] Identidad
>
> **Jugoo**, mi desktop shell personal para **Hyprland**, construida sobre CachyOS.
>
> Empezó como una modificación de Waybar y terminó convirtiéndose en una interfaz propia para mi escritorio: ventanas, workspaces, música, estadísticas, notificaciones y otros componentes del entorno.
>
> **Jugoo = JGO + Universe + Orbit.**
>
**JGO** representa a quien lo construye.
**Universe**, el entorno que estoy creando.
**Orbit**, las trayectorias que cambian cuando distintas cosas se encuentran.

> *“Do you believe in gravity?”*

Quizás algunas cosas simplemente tienen una forma de encontrarse.

**Jugoo = JGO + Universe + Orbit.**

>
> En pocas palabras: **mi propio universo, en mi propia órbita.**

---

## Tema visual

Jugoo utiliza un tema semántico centralizado en [`themes/space.toml`](themes/space.toml).

Desde ahí se controlan colores, opacidad, blur, radios y animaciones de la shell.

Con Jugoo abierta, guarda el TOML para recargar el tema automáticamente o ejecuta:

```sh
jugoo --reload-theme
```

El tema también genera:

```text
~/.config/hypr/config/jugoo_theme_generated.lua
```

para mantener la identidad visual entre Jugoo y Hyprland.

---

## Instalación

Jugoo está diseñada para vivir dentro de la configuración XDG del usuario.

Clona el repositorio en:

```text
~/.config/jugoo
```

Por ejemplo:

```sh
mkdir -p ~/.config
cd ~/.config
git clone <url-del-repo> jugoo
cd jugoo
```

### Requisitos

* Arch / CachyOS
* Hyprland
* Python 3
* GTK3 / layer-shell

Los paquetes necesarios están especificados en [`system-requirements.txt`](system-requirements.txt).

### Configuración inicial

Desde la raíz del proyecto:

```sh
python3 acomodador.py
```

El instalador configura la identidad XDG, instala los paquetes necesarios, prepara la integración con Hyprland y exporta el tema.

Opciones:

```sh
python3 acomodador.py --dry-run
python3 acomodador.py --skip-packages
python3 acomodador.py --with-optional
```

Después:

```sh
hyprctl reload
```

---

## Uso

Iniciar Jugoo:

```sh
jugoo
```

Ejecutarla directamente desde el repositorio:

```sh
python3 -m shell
```

Ver las acciones disponibles para Hyprland:

```sh
jugoo action list
```

Recargar el tema:

```sh
jugoo --reload-theme
```

---

## Datos

|                 |                                 |
| --------------- | ------------------------------- |
| **Nombre**      | Jugoo                           |
| **Tipo**        | Desktop Shell                   |
| **Entorno**     | Hyprland                        |
| **Origen**      | Waybar → proyecto independiente |
| **Significado** | JGO + Universe + Orbit          |
| **Tema**        | Space                           |
