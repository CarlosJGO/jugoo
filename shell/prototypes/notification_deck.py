"""Task 6.1 prototype: a second window slides out from behind a fixed parent.

Independent of the real notification code. Two floating toplevels:

* parent (red): fixed position and size, forced opaque so nothing behind it
  shows through the theme's window opacity;
* stack (green): mapped lazily and veiled (paints nothing) until it has been
  slotted right under the parent; then it slides between a hidden rect fully
  inside the parent and a visible rect to the parent's left. Every move is
  followed, in the same Lua chunk, by raising the stack and then the parent.

Mechanisms:

* ``A``: one ``hl.dsp.window.move`` per transition, animated by Hyprland's
  ``windowsMove``;
* ``B``: one instant move per GTK frame over the Hyprland socket, with
  ``no_anim`` on the stack window (curve and duration owned by Jugoo).

Measurements, printed as JSON:

* ``j/clients`` sampled every millisecond: the parent's position and size
  must never change;
* one screen row captured with ``grim`` on every display frame: the parent
  must stay pure red (no deformation, no stack showing through), the stack
  must not peek out at rest, and its left edge gives the motion profile.

Run inside the Hyprland session::

    PYTHONPATH=. python3 -m shell.prototypes.notification_deck --mode A
    PYTHONPATH=. python3 -m shell.prototypes.notification_deck --mode B
"""

from __future__ import annotations

import argparse
import json
import math
import os
import socket
import subprocess
import threading
import time
from dataclasses import dataclass, field

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")

from gi.repository import Gdk, GLib, Gtk

PARENT_TITLE = "Jugoo Deck Prototype Parent"
STACK_TITLE = "Jugoo Deck Prototype Stack"

PARENT_X, PARENT_Y, PARENT_W, PARENT_H = 1100, 200, 360, 420
STACK_W, STACK_H = 340, 300
GAP = 8
# Hidden rect strictly inside the parent: its 2 px border cannot peek out.
HIDDEN_X = PARENT_X + (PARENT_W - STACK_W) // 2
STACK_Y = PARENT_Y + 40
VISIBLE_X = PARENT_X - GAP - STACK_W

SCAN_Y = STACK_Y + STACK_H // 2
SCAN_X0 = VISIBLE_X - 40
SCAN_X1 = PARENT_X + PARENT_W + 40


def _socket_path() -> str:
    return os.path.join(
        os.environ["XDG_RUNTIME_DIR"],
        "hypr",
        os.environ["HYPRLAND_INSTANCE_SIGNATURE"],
        ".socket.sock",
    )


def hypr(command: str) -> str:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.connect(_socket_path())
        client.sendall(command.encode())
        client.shutdown(socket.SHUT_WR)
        chunks = []
        while chunk := client.recv(65536):
            chunks.append(chunk)
    return b"".join(chunks).decode()


def dispatch(expression: str) -> str:
    return hypr("dispatch " + expression).strip()


def move(title: str, x: int, y: int) -> None:
    dispatch(
        f'hl.dsp.window.move({{ window = "title:^{title}$", x = {x}, y = {y}, relative = false }})'
    )


def move_below(title: str, x: int, y: int) -> None:
    """Move ``title`` and slot it directly under the parent, in one Lua chunk.

    Hyprland raises a floating window it moves, and ``mode = "bottom"`` would
    sink it under every other window of the workspace. Raising the stack and
    then the parent leaves the stack right below the parent, above the rest.
    """
    stack = f'"title:^{title}$"'
    parent = f'"title:^{PARENT_TITLE}$"'
    hypr(
        "eval "
        f"hl.dispatch(hl.dsp.window.move({{ window = {stack}, x = {x}, y = {y}, relative = false }})) "
        f'hl.dispatch(hl.dsp.window.alter_zorder({{ window = {stack}, mode = "top" }})) '
        f'hl.dispatch(hl.dsp.window.alter_zorder({{ window = {parent}, mode = "top" }}))'
    )


def set_prop(title: str, prop: str, value: str) -> None:
    dispatch(
        f'hl.dsp.window.set_prop({{ window = "title:^{title}$", prop = "{prop}", value = "{value}" }})'
    )


def install_rules(mode: str) -> None:
    """Rules apply from the first mapped frame, which ``set_prop`` cannot.

    The stack has no border or shadow: either would show around the parent
    while the stack is hidden behind it. The parent is forced opaque, or the
    theme's window opacity lets the hidden stack show through it.
    """
    # Explicit in both modes: re-registering a rule name keeps old fields.
    stack_anim = f"no_anim = {'true' if mode == 'B' else 'false'},"
    hypr(
        "eval JUGOO_DECK_RULES = { "
        f'hl.window_rule({{ name = "jugoo-deck-parent", match = {{ title = "^{PARENT_TITLE}$" }}, '
        'float = true, opacity = "1.0 override 1.0 override" }), '
        f'hl.window_rule({{ name = "jugoo-deck-stack", match = {{ title = "^{STACK_TITLE}$" }}, '
        f"float = true, border_size = 0, no_shadow = true, no_focus = true, {stack_anim} }}) }}"
    )


def remove_rules() -> None:
    hypr("eval for _, rule in ipairs(JUGOO_DECK_RULES or {}) do rule:set_enabled(false) end")


def symmetric_ease(t: float) -> float:
    return 0.5 - 0.5 * math.cos(math.pi * max(0.0, min(1.0, t)))


def _is_red(px: bytes) -> bool:
    # The parent is forced opaque: (244, 9, 0) already means the stack bleeds.
    return px[0] >= 252 and px[1] <= 3 and px[2] <= 3


def _is_greenish(px: bytes) -> bool:
    return px[1] >= 120 and px[0] <= 100 and px[2] <= 100


@dataclass
class Report:
    mode: str
    cycles: int
    parent_states: set[tuple[int, int, int, int]] = field(default_factory=set)
    frames: int = 0
    parent_damaged_frames: int = 0
    peek_frames_at_rest: int = 0
    rest_frames: int = 0
    # (slide index, left edge of the stack, phase); one index per slide.
    stack_edges: list[tuple[int, int | None, str]] = field(default_factory=list)
    map_reveal_frames: int = 0
    damage_by_phase: dict[str, int] = field(default_factory=dict)
    damage_samples: dict[str, str] = field(default_factory=dict)

    def summary(self) -> dict:
        slides: dict[int, list[int | None]] = {}
        for slide, edge, phase in self.stack_edges:
            if phase in ("out", "in"):
                slides.setdefault(slide, []).append(edge)
        steps: list[int] = []
        reversals = 0
        for slide, edges in slides.items():
            sign = -1 if slide % 2 == 0 else 1
            seq = [edge for edge in edges if edge is not None]
            steps += [abs(b - a) for a, b in zip(seq, seq[1:])]
            reversals += sum(1 for a, b in zip(seq, seq[1:]) if (b - a) * sign < -1)
        moving = [edge for edges in slides.values() for edge in edges]
        # How far the stack already sticks out on its first visible frame.
        first_out = [
            PARENT_X - next(e for e in edges if e is not None)
            for slide, edges in slides.items()
            if slide % 2 == 0 and any(e is not None for e in edges)
        ]
        settle = []
        for edges in slides.values():
            final = edges[-1]
            last_change = max((i for i, e in enumerate(edges) if e != final), default=-1)
            settle.append(last_change + 1)
        return {
            "mode": self.mode,
            "cycles": self.cycles,
            "parent_geometry_states": len(self.parent_states),
            "parent_geometry": sorted(self.parent_states),
            "captured_frames": self.frames,
            "parent_damaged_frames": self.parent_damaged_frames,
            "damage_by_phase": self.damage_by_phase,
            "damage_samples": self.damage_samples,
            "rest_frames": self.rest_frames,
            "peek_frames_at_rest": self.peek_frames_at_rest,
            "stack_visible_on_top_at_map_frames": self.map_reveal_frames,
            "motion_frames": len(moving),
            "max_step_px": max(steps) if steps else 0,
            "reversed_steps": reversals,
            "frames_to_settle_max": max(settle) if settle else 0,
            "first_visible_px_max": max(first_out) if first_out else 0,
        }


class DeckPrototype:
    def __init__(self, mode: str, cycles: int, duration_ms: int) -> None:
        self.mode = mode
        self.cycles = cycles
        self.duration_ms = duration_ms
        self.report = Report(mode=mode, cycles=cycles)
        self.phase = "setup"
        self.slide_index = 0
        self._stop = threading.Event()
        self._veiled = True
        self._slide: tuple[float, int, int] | None = None
        self._stack_x = HIDDEN_X

        css = Gtk.CssProvider()
        css.load_from_data(b"#deck-parent{background:#ff0000;} #deck-stack{background:#00ff00;}")
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), css, 800)

        self.parent = self._window(PARENT_TITLE, "deck-parent", PARENT_W, PARENT_H)
        self.stack: Gtk.Window | None = None

    def _window(self, title: str, name: str, width: int, height: int) -> Gtk.Window:
        window = Gtk.Window(title=title)
        window.set_decorated(False)
        window.set_resizable(False)
        visual = window.get_screen().get_rgba_visual()
        if visual is not None:
            window.set_visual(visual)
        window.set_app_paintable(True)
        body = Gtk.Box()
        body.set_name(name)
        body.set_size_request(width, height)
        window.add(body)
        return window

    # -- lifecycle -----------------------------------------------------------

    def run(self) -> dict:
        install_rules(self.mode)
        try:
            self.parent.show_all()
            GLib.timeout_add(150, self._place_parent)
            Gtk.main()
        finally:
            remove_rules()
        return self.report.summary()

    def _place_parent(self) -> bool:
        move(PARENT_TITLE, PARENT_X, PARENT_Y)
        # The rule's opacity string leaves ~0.96: the hidden stack bleeds through.
        for prop in ("opacity_override", "opacity_inactive_override", "opacity"):
            set_prop(PARENT_TITLE, prop, "1")
        threading.Thread(target=self._sample_clients, daemon=True).start()
        threading.Thread(target=self._capture_frames, daemon=True).start()
        # Let the parent's own placement animation finish before measuring.
        GLib.timeout_add(900, self._map_stack)
        return False

    def _map_stack(self) -> bool:
        self.phase = "map"
        stack = self._window(STACK_TITLE, "deck-stack", STACK_W, STACK_H)
        stack.connect("draw", self._on_stack_draw)
        stack.connect("map-event", self._on_stack_mapped)
        self.stack = stack
        stack.show_all()
        return False

    def _on_stack_draw(self, _widget: Gtk.Widget, cr) -> bool:
        if not self._veiled:
            return False
        cr.set_operator(0)  # CAIRO_OPERATOR_CLEAR
        cr.paint()
        return True

    def _on_stack_mapped(self, *_args) -> bool:
        GLib.idle_add(self._sink_stack)
        return False

    def _sink_stack(self) -> bool:
        move_below(STACK_TITLE, HIDDEN_X, STACK_Y)
        self._veiled = False
        assert self.stack is not None
        self.stack.queue_draw()
        GLib.timeout_add(700, self._next_cycle, 0)
        return False

    def _next_cycle(self, index: int) -> bool:
        if index >= self.cycles:
            self.phase = "teardown"
            GLib.timeout_add(150, self._teardown)
            return False
        self.phase = "rest"
        GLib.timeout_add(250, self._start_slide, "out", index)
        return False

    def _start_slide(self, direction: str, index: int) -> bool:
        self.slide_index = 2 * index + (0 if direction == "out" else 1)
        self.phase = direction
        target = VISIBLE_X if direction == "out" else HIDDEN_X
        if self.mode == "A":
            move_below(STACK_TITLE, target, STACK_Y)
            settle = 650
        else:
            self._slide = (time.monotonic(), self._stack_x, target)
            assert self.stack is not None
            self.stack.add_tick_callback(self._slide_tick)
            settle = self.duration_ms + 120
        self._stack_x = target if self.mode == "A" else self._stack_x
        if direction == "out":
            GLib.timeout_add(settle, self._hold_visible, index)
        else:
            GLib.timeout_add(settle, self._next_cycle, index + 1)
        return False

    def _hold_visible(self, index: int) -> bool:
        self.phase = "visible"
        GLib.timeout_add(250, self._start_slide, "in", index)
        return False

    def _slide_tick(self, _widget: Gtk.Widget, _clock) -> bool:
        if self._slide is None:
            return False
        started, start_x, target_x = self._slide
        t = (time.monotonic() - started) * 1000.0 / max(1, self.duration_ms)
        x = round(start_x + (target_x - start_x) * symmetric_ease(t))
        if x != self._stack_x:
            self._stack_x = x
            move_below(STACK_TITLE, x, STACK_Y)
        if t >= 1.0:
            self._slide = None
            return False
        return True

    def _teardown(self) -> bool:
        self._stop.set()
        if self.stack is not None:
            self.stack.destroy()
        self.parent.destroy()
        Gtk.main_quit()
        return False

    # -- measurement ---------------------------------------------------------

    def _sample_clients(self) -> None:
        while not self._stop.is_set():
            try:
                for client in json.loads(hypr("j/clients")):
                    if client["title"] == PARENT_TITLE:
                        self.report.parent_states.add(
                            (*client["at"], *client["size"])  # type: ignore[arg-type]
                        )
            except (OSError, ValueError):
                pass
            time.sleep(0.001)

    def _capture_frames(self) -> None:
        width = SCAN_X1 - SCAN_X0
        geometry = f"{SCAN_X0},{SCAN_Y} {width}x1"
        while not self._stop.is_set():
            phase = self.phase
            raw = subprocess.run(
                ["grim", "-g", geometry, "-t", "ppm", "-"], capture_output=True, check=False
            ).stdout
            if not raw:
                continue
            row = raw.split(b"\n", 3)[3]
            pixel = lambda x: row[3 * (x - SCAN_X0) : 3 * (x - SCAN_X0) + 3]  # noqa: E731
            if phase == "setup":
                continue
            self.report.frames += 1
            bad = [x for x in range(PARENT_X, PARENT_X + PARENT_W) if not _is_red(pixel(x))]
            if bad:
                self.report.parent_damaged_frames += 1
                by_phase = self.report.damage_by_phase
                by_phase[phase] = by_phase.get(phase, 0) + 1
                self.report.damage_samples.setdefault(
                    phase,
                    f"x={bad[0] - PARENT_X}..{bad[-1] - PARENT_X} n={len(bad)} rgb={tuple(pixel(bad[0]))}",
                )
            green = [x for x in range(SCAN_X0, PARENT_X) if _is_greenish(pixel(x))]
            if phase == "map" and any(
                _is_greenish(pixel(x)) for x in range(PARENT_X, PARENT_X + PARENT_W)
            ):
                self.report.map_reveal_frames += 1
            if phase == "rest":
                self.report.rest_frames += 1
                if green:
                    self.report.peek_frames_at_rest += 1
            if phase in ("out", "in", "visible"):
                self.report.stack_edges.append((self.slide_index, green[0] if green else None, phase))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mode", choices=("A", "B"), default="A")
    parser.add_argument("--cycles", type=int, default=5)
    parser.add_argument("--duration-ms", type=int, default=220)
    args = parser.parse_args()
    print(json.dumps(DeckPrototype(args.mode, args.cycles, args.duration_ms).run(), indent=2))


if __name__ == "__main__":
    main()
