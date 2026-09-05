from __future__ import annotations

import os
from pathlib import Path

from shell.desktop_install import install_identity, uninstall_identity
from shell.identity import APPLICATION_ID, COMMAND_NAME, ICON_NAME, discover_logo


def test_discover_logo_skips_placeholder(tmp_path: Path) -> None:
    (tmp_path / "logo-placeholder.svg").write_text("<svg></svg>", encoding="utf-8")
    (tmp_path / "jugoo.svg").write_text("<svg></svg>", encoding="utf-8")

    assert discover_logo(tmp_path).name == "jugoo.svg"


def test_discover_logo_prefers_application_id(tmp_path: Path) -> None:
    (tmp_path / "logo.png").write_bytes(b"not-a-png")
    (tmp_path / f"{APPLICATION_ID}.svg").write_text("<svg></svg>", encoding="utf-8")

    assert discover_logo(tmp_path).name == f"{APPLICATION_ID}.svg"


def test_discover_logo_empty_directory(tmp_path: Path) -> None:
    assert discover_logo(tmp_path) is None


def test_identity_install_is_idempotent(tmp_path: Path) -> None:
    data_home = tmp_path / "share"
    bin_home = tmp_path / "bin"
    config_home = tmp_path / "config"
    previous_data = os.environ.get("XDG_DATA_HOME")
    previous_bin = os.environ.get("XDG_BIN_HOME")
    previous_config = os.environ.get("XDG_CONFIG_HOME")
    os.environ["XDG_DATA_HOME"] = str(data_home)
    os.environ["XDG_BIN_HOME"] = str(bin_home)
    os.environ["XDG_CONFIG_HOME"] = str(config_home)
    try:
        empty_assets = tmp_path / "assets"
        empty_assets.mkdir()
        assert install_identity(assets=empty_assets) == 0
        assert install_identity(assets=empty_assets) == 0

        desktop = data_home / "applications" / f"{APPLICATION_ID}.desktop"
        wrapper = bin_home / COMMAND_NAME
        service = config_home / "systemd" / "user" / "jugoo-task-watcher.service"
        payload = desktop.read_text(encoding="utf-8")
        unit = service.read_text(encoding="utf-8")
        assert desktop.is_file()
        assert wrapper.is_file()
        assert service.is_file()
        assert os.access(wrapper, os.X_OK)
        assert "Name=Jugoo" in payload
        assert f"Exec={wrapper}" in payload
        assert f"StartupWMClass={APPLICATION_ID}" in payload
        assert "Icon=" not in payload
        assert wrapper.read_text(encoding="utf-8").count("python3 -m shell") == 1
        assert "--task-watcher" in unit
        assert "Restart=on-failure" in unit

        assert uninstall_identity() == 0
        assert not desktop.exists()
        assert not wrapper.exists()
        assert not service.exists()
    finally:
        _restore_env("XDG_DATA_HOME", previous_data)
        _restore_env("XDG_BIN_HOME", previous_bin)
        _restore_env("XDG_CONFIG_HOME", previous_config)


def test_identity_install_copies_svg_logo(tmp_path: Path) -> None:
    data_home = tmp_path / "share"
    bin_home = tmp_path / "bin"
    config_home = tmp_path / "config"
    previous_data = os.environ.get("XDG_DATA_HOME")
    previous_bin = os.environ.get("XDG_BIN_HOME")
    previous_config = os.environ.get("XDG_CONFIG_HOME")
    os.environ["XDG_DATA_HOME"] = str(data_home)
    os.environ["XDG_BIN_HOME"] = str(bin_home)
    os.environ["XDG_CONFIG_HOME"] = str(config_home)
    try:
        assets = tmp_path / "assets"
        assets.mkdir()
        (assets / "jugoo.svg").write_text(
            "<svg xmlns='http://www.w3.org/2000/svg'></svg>\n",
            encoding="utf-8",
        )
        assert install_identity(assets=assets) == 0
        installed = data_home / "icons" / "hicolor" / "scalable" / "apps" / f"{ICON_NAME}.svg"
        desktop = data_home / "applications" / f"{APPLICATION_ID}.desktop"
        assert installed.is_file()
        assert f"Icon={ICON_NAME}" in desktop.read_text(encoding="utf-8")
        assert uninstall_identity() == 0
    finally:
        _restore_env("XDG_DATA_HOME", previous_data)
        _restore_env("XDG_BIN_HOME", previous_bin)
        _restore_env("XDG_CONFIG_HOME", previous_config)


def _restore_env(name: str, value: str | None) -> None:
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value


def _run() -> None:
    import inspect
    import tempfile

    namespace = {name: value for name, value in globals().items() if name.startswith("test_")}
    for name, test in sorted(namespace.items()):
        parameters = inspect.signature(test).parameters
        if "tmp_path" in parameters:
            with tempfile.TemporaryDirectory() as folder:
                test(Path(folder))
        else:
            test()
        print(f"ok {name}")


if __name__ == "__main__":
    _run()
