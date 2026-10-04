"""Remove only the selected Eidolon home's GUI artifacts; preserve other products."""

import os
import shutil
import sys
from pathlib import Path

from hermes_constants import get_hermes_home
from hermes_cli.colors import Colors, color


def _logger(mark: str, col: str):
    return lambda msg: print(f"{color(mark, col)} {msg}")


log_info, log_success = _logger("→", Colors.CYAN), _logger("✓", Colors.GREEN)
log_warn = _logger("⚠", Colors.YELLOW)


def _env_dir(var: str, fallback: Path) -> Path:
    return Path(value).expanduser().absolute() if (value := os.environ.get(var)) else fallback


def owned_child(path: Path, home: Path) -> bool:
    """The leaf may be a symlink (unlink only); no parent may escape the selected home."""
    root = home.resolve()
    if path.is_symlink():
        return path.parent.resolve().is_relative_to(root)
    resolved = path.resolve()
    return resolved != root and resolved.is_relative_to(root)


def desktop_userdata_dir(hermes_home: Path | None = None) -> Path:
    """Match Electron's selected-home userData resolver, never legacy global Hermes data."""
    home = hermes_home if hermes_home is not None else get_hermes_home()
    return _env_dir("HERMES_DESKTOP_USER_DATA_DIR", home / "desktop")


def source_built_gui_artifacts(hermes_home: Path) -> list[Path]:
    agent_root = hermes_home / "hermes-agent"
    desktop_dir = agent_root / "apps" / "desktop"
    candidates = [desktop_dir / "dist", desktop_dir / "release", desktop_dir / "node_modules",
                  agent_root / "node_modules", hermes_home / "desktop-build-stamp.json"]
    return [p for p in candidates if owned_child(p, hermes_home)]


def packaged_gui_app_paths(hermes_home: Path | None = None) -> list[Path]:
    """Only a Linux launcher explicitly marked for this home can prove ownership.

    Global macOS/Windows bundles belong to their OS installer, not a selected runtime home.
    Leave them for manual uninstall instead of deleting either product's shared app bundle.
    """
    if not sys.platform.startswith(("linux", "freebsd", "openbsd", "netbsd")):
        return []
    home = hermes_home if hermes_home is not None else get_hermes_home()
    from hermes_cli.linux_desktop_entry import desktop_entry_path
    entry = desktop_entry_path()
    data = _env_dir("XDG_DATA_HOME", Path.home() / ".local" / "share")
    try:
        fields = dict(line.split("=", 1) for line in entry.read_text(encoding="utf-8").splitlines()
                      if "=" in line and not line.lstrip().startswith("#"))
        if fields.get("X-Eidolon-Home") != str(home.resolve()) or not owned_child(entry, data):
            return []
    except (OSError, ValueError):
        return []
    candidates = [entry] + [data / "icons" / "hicolor" / size / "apps" / "eidolon.png"
                           for size in ("scalable", "24x24", "32x32", "48x48", "256x256", "512x512", "1024x1024")]
    return [p for p in candidates if owned_child(p, data)]


def agent_is_installed(hermes_home: Path) -> bool:
    return any((hermes_home / "hermes-agent" / sub).is_dir() for sub in ("hermes_cli", "venv", ".venv"))


def gui_is_installed(hermes_home: Path) -> bool:
    summary = gui_install_summary(hermes_home)
    return summary["gui_installed"]


def gui_install_summary(hermes_home: Path | None = None) -> dict:
    home = hermes_home if hermes_home is not None else get_hermes_home()
    userdata = desktop_userdata_dir(home)
    artifacts = [str(p) for p in source_built_gui_artifacts(home) if p.exists() or p.is_symlink()]
    packaged = [str(p) for p in packaged_gui_app_paths(home) if p.exists() or p.is_symlink()]
    userdata_exists = owned_child(userdata, home) and (userdata.exists() or userdata.is_symlink())
    return {"hermes_home": str(home), "agent_installed": agent_is_installed(home),
            "gui_installed": bool(artifacts or packaged or userdata_exists),
            "source_built_artifacts": artifacts, "packaged_app_paths": packaged,
            "userdata_dir": str(userdata), "userdata_exists": userdata_exists, "platform": sys.platform,
            "manual_userdata": str(userdata) if not owned_child(userdata, home) else None}


def _remove_path(path: Path) -> bool:
    try:
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)
        else:
            return False
        return True
    except Exception as e:
        log_warn(f"Could not remove {path}: {e}")
        return False


def uninstall_gui(hermes_home: Path | None = None, *, remove_userdata: bool = True) -> list[Path]:
    """Remove proven selected-home GUI state, never shared bundles or another home's data."""
    home = hermes_home if hermes_home is not None else get_hermes_home()
    paths = [*source_built_gui_artifacts(home), *packaged_gui_app_paths(home)]
    userdata = desktop_userdata_dir(home)
    if remove_userdata:
        if owned_child(userdata, home):
            paths.append(userdata)
        else:
            log_warn(f"Preserved desktop data outside the selected home: {userdata}. Review and remove it manually.")
    removed = []
    for path in paths:
        if _remove_path(path):
            log_success(f"Removed {path}")
            removed.append(path)
    log_info("Global packaged apps and unverified launchers were preserved. Use your OS app/package manager "
             "to remove the Eidolon bundle after verifying its location; do not remove Hermes installations.")
    if sys.platform.startswith("linux"):
        from hermes_cli.linux_desktop_entry import desktop_entry_path, refresh_desktop_databases
        entry = desktop_entry_path()
        if entry in removed:
            for tool in refresh_desktop_databases(entry.parent):
                log_success(f"Refreshed the application menu cache ({tool})")
    return removed
