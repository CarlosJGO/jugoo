"""Tests for Jugoo SDDM staging, config merge, and privileged helper (mocked)."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from shell.servicios.sddm.assets import install_background
from shell.servicios.sddm.conf import SddmThemeOptions, render_theme_conf
from shell.servicios.sddm.effective import read_effective_current
from shell.servicios.sddm.service import SddmService
from shell.ui.theme import load_theme
from shell.identity import project_root

HELPER_PATH = project_root() / "shell" / "helpers" / "jugoo-sddm-helper"


def _load_helper_module():
    loader = importlib.machinery.SourceFileLoader("jugoo_sddm_helper", str(HELPER_PATH))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["jugoo_sddm_helper"] = module
    loader.exec_module(module)
    return module


def test_read_effective_current_respects_merge_order(tmp_path: Path) -> None:
    lib = tmp_path / "lib"
    etc_d = tmp_path / "etc.d"
    etc = tmp_path / "sddm.conf"
    lib.mkdir()
    etc_d.mkdir()
    (lib / "00-base.conf").write_text("[Theme]\nCurrent=maya\n", encoding="utf-8")
    (etc_d / "10-jugoo.conf").write_text("[Theme]\nCurrent=jugoo\n", encoding="utf-8")
    etc.write_text("[Autologin]\nSession=hyprland\n\n[Theme]\nCurrent=astronaut\n", encoding="utf-8")

    paths = (
        lib / "00-base.conf",
        etc_d / "10-jugoo.conf",
        etc,
    )
    assert read_effective_current(paths=paths) == "astronaut"


def test_install_background_unicode_and_spaces(tmp_path: Path) -> None:
    source = tmp_path / "Fondos" / "kore ga yuuji da.jpg"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"\xff\xd8\xffjpeg-bytes")
    dest_dir = tmp_path / "Backgrounds"
    rel = install_background(source, dest_dir)
    assert rel.startswith("Backgrounds/user-")
    assert rel.endswith(".jpg")
    installed = dest_dir / Path(rel).name
    assert installed.is_file()
    assert installed.read_bytes() == source.read_bytes()
    assert " " not in installed.name


def test_install_background_png(tmp_path: Path) -> None:
    source = tmp_path / "nebula.png"
    source.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    rel = install_background(source, tmp_path / "Backgrounds")
    assert rel.endswith(".png")


def test_render_theme_conf_uses_space_palette() -> None:
    theme = load_theme(project_root() / "themes" / "space.toml")
    text = render_theme_conf(
        theme,
        SddmThemeOptions(
            background="Backgrounds/user-abc.jpg",
            background_dim=0.4,
            background_fill="crop",
            show_avatar=True,
            show_clock=False,
            form_opacity=0.9,
            form_radius=12,
        ),
    )
    assert "background=Backgrounds/user-abc.jpg" in text
    assert "showClock=false" in text
    assert theme.colors.primary in text
    assert theme.colors.accent in text


def test_build_staging_copies_template_and_background(tmp_path: Path) -> None:
    theme_src = project_root() / "shell" / "assets" / "sddm" / "jugoo"
    bg = tmp_path / "mi fondo 宇宙.png"
    bg.write_bytes(b"\x89PNG\r\n\x1a\nfake-png")

    service = SddmService(
        theme_source=theme_src,
        staging_root=tmp_path / "stage",
        theme_provider=lambda: load_theme(project_root() / "themes" / "space.toml"),
    )
    staging = service.build_staging(
        {
            "sddm.background_path": str(bg),
            "sddm.background_dim": 0.3,
            "sddm.background_fill": "fit",
            "sddm.show_avatar": True,
            "sddm.show_clock": True,
            "sddm.form_opacity": 0.85,
            "sddm.form_radius": 14,
        }
    )
    assert (staging / "Main.qml").is_file()
    assert (staging / "metadata.desktop").is_file()
    conf = (staging / "theme.conf").read_text(encoding="utf-8")
    assert "backgroundFill=fit" in conf
    assert "formRadius=14" in conf
    assert any((staging / "Backgrounds").glob("user-*.png"))


def test_strip_theme_current_preserves_autologin() -> None:
    helper = _load_helper_module()
    original = "[Autologin]\nSession=hyprland\n\n[Theme]\nCurrent=astronaut\n"
    rewritten = helper._strip_theme_current_text(original)
    assert "[Autologin]" in rewritten
    assert "Session=hyprland" in rewritten
    assert "Current=" not in rewritten
    # Empty Theme section removed
    assert "[Theme]" not in rewritten


def test_helper_apply_then_restore_roundtrip(tmp_path: Path, monkeypatch) -> None:
    helper = _load_helper_module()

    themes = tmp_path / "themes"
    themes.mkdir()
    dropin_dir = tmp_path / "sddm.conf.d"
    dropin_dir.mkdir()
    sddm_conf = tmp_path / "sddm.conf"
    sddm_conf.write_text(
        "[Autologin]\nSession=hyprland\n\n[Theme]\nCurrent=astronaut\n",
        encoding="utf-8",
    )
    state_dir = tmp_path / "state"
    original_autologin = sddm_conf.read_text(encoding="utf-8")

    monkeypatch.setattr(helper, "THEME_DIR", themes / "jugoo")
    monkeypatch.setattr(helper, "DROPIN_DIR", dropin_dir)
    monkeypatch.setattr(helper, "DROPIN_PATH", dropin_dir / "10-jugoo.conf")
    monkeypatch.setattr(helper, "STATE_DIR", state_dir)
    monkeypatch.setattr(helper, "STATE_PATH", state_dir / "state.json")
    monkeypatch.setattr(helper, "SDDM_CONF", sddm_conf)
    monkeypatch.setattr(helper, "SDDM_CONF_BACKUP", state_dir / "sddm.conf.bak")
    monkeypatch.setattr(helper, "_read_effective_current", lambda: _eff(helper))

    # Build minimal staging
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "Main.qml").write_text("// ok\n", encoding="utf-8")
    (staging / "metadata.desktop").write_text("[SddmGreeterTheme]\n", encoding="utf-8")
    (staging / "theme.conf").write_text("[General]\nbackground=\n", encoding="utf-8")

    monkeypatch.setattr(helper, "os", type("O", (), {"geteuid": staticmethod(lambda: 0)})())

    assert helper.cmd_apply(staging) == 0
    assert (themes / "jugoo" / "Main.qml").is_file()
    assert (dropin_dir / "10-jugoo.conf").is_file()
    assert "Current=jugoo" in (dropin_dir / "10-jugoo.conf").read_text(encoding="utf-8")
    assert "Current=" not in sddm_conf.read_text(encoding="utf-8")
    assert "Session=hyprland" in sddm_conf.read_text(encoding="utf-8")
    state = json.loads((state_dir / "state.json").read_text(encoding="utf-8"))
    assert state["previous_current"] == "astronaut"

    assert helper.cmd_restore() == 0
    assert not (dropin_dir / "10-jugoo.conf").exists()
    restored = sddm_conf.read_text(encoding="utf-8")
    assert restored == original_autologin
    assert "Current=astronaut" in restored
    assert "Session=hyprland" in restored


def _eff(helper) -> str:
    """Effective Current using monkeypatched paths."""
    paths = []
    if helper.DROPIN_DIR.is_dir():
        paths.extend(sorted(helper.DROPIN_DIR.glob("*.conf")))
    if helper.SDDM_CONF.is_file():
        paths.append(helper.SDDM_CONF)
    current = ""
    for path in paths:
        value = helper._read_theme_current(path)
        if value is not None:
            current = value.strip()
    return current


def test_helper_aborts_activation_on_incomplete_theme(tmp_path: Path, monkeypatch) -> None:
    helper = _load_helper_module()
    themes = tmp_path / "themes"
    themes.mkdir()
    dropin_dir = tmp_path / "sddm.conf.d"
    dropin_dir.mkdir()
    sddm_conf = tmp_path / "sddm.conf"
    sddm_conf.write_text("[Theme]\nCurrent=astronaut\n", encoding="utf-8")
    state_dir = tmp_path / "state"

    monkeypatch.setattr(helper, "THEME_DIR", themes / "jugoo")
    monkeypatch.setattr(helper, "DROPIN_DIR", dropin_dir)
    monkeypatch.setattr(helper, "DROPIN_PATH", dropin_dir / "10-jugoo.conf")
    monkeypatch.setattr(helper, "STATE_DIR", state_dir)
    monkeypatch.setattr(helper, "STATE_PATH", state_dir / "state.json")
    monkeypatch.setattr(helper, "SDDM_CONF", sddm_conf)
    monkeypatch.setattr(helper, "SDDM_CONF_BACKUP", state_dir / "sddm.conf.bak")
    monkeypatch.setattr(helper, "os", type("O", (), {"geteuid": staticmethod(lambda: 0)})())

    staging = tmp_path / "bad"
    staging.mkdir()
    (staging / "Main.qml").write_text("x", encoding="utf-8")
    # missing metadata + theme.conf

    with pytest.raises(FileNotFoundError):
        helper.cmd_apply(staging)
    assert not (dropin_dir / "10-jugoo.conf").exists()
    assert "Current=astronaut" in sddm_conf.read_text(encoding="utf-8")


def test_service_apply_enabled_false_calls_restore(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def fake_pkexec(argv: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=json.dumps(
                {"ok": True, "message": "restored", "effective_current": "astronaut"}
            ),
            stderr="",
        )

    helper = tmp_path / "helper"
    helper.write_text("#!/bin/true\n", encoding="utf-8")
    helper.chmod(0o755)
    policy = tmp_path / "policy"
    policy.write_text("<policyconfig/>", encoding="utf-8")

    service = SddmService(
        theme_source=project_root() / "shell" / "assets" / "sddm" / "jugoo",
        staging_root=tmp_path / "stage",
        helper_source=helper,
        policy_source=policy,
        pkexec_runner=fake_pkexec,
        theme_provider=lambda: load_theme(project_root() / "themes" / "space.toml"),
    )
    # Pretend helper already installed
    import shell.servicios.sddm.service as svc_mod

    original_helper = svc_mod.SYSTEM_HELPER
    original_policy = svc_mod.SYSTEM_POLICY
    try:
        svc_mod.SYSTEM_HELPER = helper
        svc_mod.SYSTEM_POLICY = policy
        result = service.apply({"sddm.enabled": False})
    finally:
        svc_mod.SYSTEM_HELPER = original_helper
        svc_mod.SYSTEM_POLICY = original_policy

    assert result.ok
    assert calls and calls[-1][-1] == "restore"


def test_apply_async_runs_off_caller_thread(tmp_path: Path) -> None:
    import threading

    caller = threading.get_ident()
    worker_ids: list[int] = []
    results: list[bool] = []
    done = threading.Event()

    def fake_pkexec(argv: list[str]):
        worker_ids.append(threading.get_ident())
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=json.dumps({"ok": True, "message": "ok", "effective_current": "jugoo"}),
            stderr="",
        )

    import shell.servicios.sddm.service as svc_mod

    helper = tmp_path / "helper"
    helper.write_text("#!/bin/true\n", encoding="utf-8")
    helper.chmod(0o755)
    policy = tmp_path / "policy"
    policy.write_text("<policyconfig/>", encoding="utf-8")

    service = SddmService(
        theme_source=project_root() / "shell" / "assets" / "sddm" / "jugoo",
        staging_root=tmp_path / "stage",
        helper_source=helper,
        policy_source=policy,
        pkexec_runner=fake_pkexec,
        theme_provider=lambda: load_theme(project_root() / "themes" / "space.toml"),
    )
    original_helper = svc_mod.SYSTEM_HELPER
    original_policy = svc_mod.SYSTEM_POLICY
    try:
        svc_mod.SYSTEM_HELPER = helper
        svc_mod.SYSTEM_POLICY = policy
        ok = service.apply_async(
            {"sddm.enabled": True, "sddm.background_path": ""},
            lambda result: (results.append(result.ok), done.set()),
        )
        assert ok
        assert done.wait(timeout=5)
    finally:
        svc_mod.SYSTEM_HELPER = original_helper
        svc_mod.SYSTEM_POLICY = original_policy

    assert worker_ids
    assert worker_ids[0] != caller
    assert results == [True]


def test_actions_include_sddm() -> None:
    from shell.actions import known_action_names, resolve_actions_from_argv

    assert "sddm-apply" in known_action_names()
    assert "sddm-restore" in known_action_names()
    assert resolve_actions_from_argv(["action", "sddm-apply"]) == ("sddm-apply",)
    assert resolve_actions_from_argv(["action", "sddm-restore"]) == ("sddm-restore",)


def test_ensure_polkit_agent_message_when_missing(monkeypatch, tmp_path: Path) -> None:
    from shell.servicios.sddm import polkit_agent as agent_mod

    monkeypatch.setattr(agent_mod, "_hypr_agent_running", lambda: False)
    monkeypatch.setattr(agent_mod, "HYPR_AGENT_BIN", tmp_path / "missing-hyprpolkitagent")
    status = agent_mod.ensure_polkit_agent()
    assert status.ok is False
    assert "hyprpolkitagent" in status.message


def test_default_pkexec_refuses_without_agent(monkeypatch) -> None:
    from shell.servicios.sddm import service as svc_mod
    from shell.servicios.sddm.polkit_agent import PolkitAgentStatus

    monkeypatch.setattr(
        svc_mod,
        "ensure_polkit_agent",
        lambda: PolkitAgentStatus(ok=False, kind="none", message="missing agent"),
    )
    result = svc_mod._default_pkexec(["/bin/true"])
    assert result.returncode == 126
    assert "missing agent" in result.stderr


def test_sddm_cli_argv_intercept_condition() -> None:
    """Regression: list slices must not be compared to tuples (always False)."""
    argv = ["jugoo", "action", "sddm-apply"]
    assert len(argv) >= 3 and argv[1] == "action" and argv[2] in {
        "sddm-apply",
        "sddm-restore",
    }
    # The broken form that shipped initially:
    assert not (argv[2:3] in (("sddm-apply",), ("sddm-restore",)))
