"""Side-by-side installations survive selected-root cleanup; no real uninstall is run."""

from pathlib import Path

from hermes_cli import gui_uninstall as gui
from hermes_cli import linux_desktop_entry as desktop
from hermes_cli import uninstall


def _installation(root):
    checkout = root / "hermes-agent"
    (checkout / "apps" / "desktop" / "dist").mkdir(parents=True)
    (checkout / "apps" / "desktop" / "dist" / "index.html").write_text("app")
    (root / "desktop").mkdir()
    (root / "desktop" / "state.json").write_text("private")
    (root / "config.yaml").write_text("keep: true")
    return checkout


def test_gui_cleanup_preserves_upstream_and_external_userdata(tmp_path, monkeypatch):
    selected, upstream = tmp_path / "eidolon", tmp_path / "hermes"
    own_checkout, upstream_checkout = _installation(selected), _installation(upstream)
    data = tmp_path / "xdg"
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("XDG_DATA_HOME", str(data))
    monkeypatch.delenv("HERMES_DESKTOP_USER_DATA_DIR", raising=False)
    monkeypatch.setattr(desktop, "refresh_desktop_databases", lambda _path: [])
    entry = desktop.desktop_entry_path()
    entry.parent.mkdir(parents=True)
    entry.write_text(f"[Desktop Entry]\nName=Eidolon\nX-Eidolon-Home={selected.resolve()}\n")
    upstream_entry = entry.with_name("hermes.desktop")
    upstream_entry.write_text("[Desktop Entry]\nName=Hermes\n")
    icons = data / "icons" / "hicolor" / "256x256" / "apps"
    icons.mkdir(parents=True)
    (icons / "eidolon.png").write_bytes(b"ours")
    (icons / "hermes.png").write_bytes(b"upstream")

    gui.uninstall_gui(selected)
    assert not (selected / "desktop").exists()
    assert not (own_checkout / "apps" / "desktop" / "dist").exists()
    assert (selected / "config.yaml").exists()
    assert not entry.exists() and not (icons / "eidolon.png").exists()
    assert upstream_entry.exists() and (icons / "hermes.png").exists()
    assert (upstream / "desktop" / "state.json").exists()

    monkeypatch.setenv("HERMES_DESKTOP_USER_DATA_DIR", str(upstream / "desktop"))
    assert gui.gui_install_summary(selected)["userdata_exists"] is False
    gui.uninstall_gui(selected)
    assert (upstream / "desktop" / "state.json").exists()
    (selected / "desktop").mkdir()
    (selected / ".env").write_text("preserve")
    monkeypatch.setenv("HERMES_DESKTOP_USER_DATA_DIR", str(selected / "desktop" / ".."))
    gui.uninstall_gui(selected)
    assert (selected / "config.yaml").exists() and (selected / ".env").exists()
    alias_home = tmp_path / "alias-home"
    alias_home.mkdir()
    (alias_home / "hermes-agent").symlink_to(upstream_checkout, target_is_directory=True)
    gui.uninstall_gui(alias_home)
    assert (upstream_checkout / "apps" / "desktop" / "dist" / "index.html").exists()


def test_lite_uninstall_and_registry_edits_are_selected_install_only(tmp_path, monkeypatch):
    selected, upstream = tmp_path / "eidolon", tmp_path / "hermes"
    checkout, upstream_checkout = _installation(selected), _installation(upstream)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(selected))
    monkeypatch.delenv("HERMES_DESKTOP_USER_DATA_DIR", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    bins = tmp_path / "bin"
    bins.mkdir()
    other_bins = tmp_path / "other-bin"
    other_bins.mkdir()
    misleading = other_bins / "hermes-agent"
    misleading.write_text(f'#!/bin/sh\nexec "{upstream_checkout}/venv/bin/hermes" "{checkout}"\n')
    monkeypatch.setattr(uninstall, "_node_symlink_candidate_dirs", lambda: [bins, other_bins])
    (bins / "hermes").write_text(f'#!/bin/sh\nexec "{checkout}/venv/bin/python" -m hermes_cli.main "$@"\n')
    (bins / "hermes-acp").write_text(f'#!/bin/sh\nexec "{upstream_checkout}/venv/bin/python" -m hermes_cli.main "$@"\n')
    (bins / "hermes-agent").write_text(f'#!/bin/sh\n# {checkout}\nexec hermes-agent "$@"\n')
    rc = tmp_path / ".zshrc"
    shared = 'export PATH="$HOME/.local/bin:$PATH"\n'
    foreign = f'export PATH="{upstream_checkout}/venv/bin:$PATH"\n'
    rc.write_text(shared + foreign + f'# Eidolon\nexport PATH="{checkout}/venv/bin:$PATH"\n')
    uninstall._perform_uninstall(project_root=checkout, hermes_home=selected, full_uninstall=False,
                                 remove_profiles=False, named_profiles=[])
    assert not checkout.exists() and upstream_checkout.exists()
    assert (selected / "config.yaml").exists()
    assert not (bins / "hermes").exists()
    assert (bins / "hermes-acp").exists() and (bins / "hermes-agent").exists()
    assert misleading.exists()
    assert rc.read_text() == shared + foreign

    class Registry:
        values = {"HERMES_HOME": r"C:\Upstream", "HERMES_GIT_BASH_PATH": r"C:\Eidolon\git\bin\bash.exe",
                  "Path": r"C:\Eidolon\node;C:\Eidolon\node-old;C:\Upstream\node"}
        def QueryValueEx(self, key, name):
            return self.values[name], 1
        def DeleteValue(self, key, name):
            del self.values[name]
        def SetValueEx(self, key, name, reserved, kind, value):
            self.values[name] = value
    registry = Registry()
    def edit(callback, **kwargs):
        removed = []
        callback(registry, None, removed)
        return removed
    monkeypatch.setattr(uninstall, "_edit_user_environment", edit)
    assert uninstall.remove_hermes_env_vars_windows(Path(r"C:\Eidolon")) == ["HERMES_GIT_BASH_PATH"]
    assert registry.values["HERMES_HOME"] == r"C:\Upstream"
    assert uninstall.remove_path_from_windows_registry(Path(r"C:\Eidolon")) == [r"C:\Eidolon\node"]
    assert registry.values["Path"] == r"C:\Eidolon\node-old;C:\Upstream\node"
