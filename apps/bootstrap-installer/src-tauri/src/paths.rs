//! Filesystem paths + logging setup.
//!
//! Matches Eidolon's Python runtime, Electron desktop, and install scripts:
//! the default is ~/.eidolon on every platform, including Windows.
//! HERMES_HOME remains an explicit compatibility override. Never discover,
//! adopt, or migrate a pre-existing Hermes installation implicitly.

use std::path::{Path, PathBuf};
#[cfg(target_os = "macos")]
use std::process::Command;
use tracing_appender::non_blocking::WorkerGuard;

/// Consume the public home alias before background tasks or child profile overrides.
pub fn normalize_home_env() {
    if let Ok(home) = std::env::var("EIDOLON_HOME") {
        if !home.trim().is_empty() {
            std::env::set_var("HERMES_HOME", home);
        }
    }
    std::env::remove_var("EIDOLON_HOME");
}

/// Returns the isolated Eidolon home, respecting an explicit HERMES_HOME override.
pub fn eidolon_home() -> PathBuf {
    resolve_home(std::env::var("HERMES_HOME").ok(), dirs::home_dir())
}

fn resolve_home(override_path: Option<String>, user_home: Option<PathBuf>) -> PathBuf {
    if let Some(override_path) = override_path {
        if !override_path.trim().is_empty() {
            return PathBuf::from(override_path);
        }
    }

    // Fork isolation is intentional: an existing .hermes profile must never
    // redirect this installer into another product's code or credentials.
    if let Some(home) = user_home {
        return home.join(".eidolon");
    }

    // Last resort — current dir, almost certainly wrong but at least
    // doesn't panic.
    PathBuf::from(".eidolon")
}

pub fn log_dir() -> PathBuf {
    eidolon_home().join("logs")
}

pub fn log_path() -> PathBuf {
    log_dir().join("bootstrap-installer.log")
}

pub fn bootstrap_cache_dir() -> PathBuf {
    eidolon_home().join("bootstrap-cache")
}

/// Stable location the installer copies itself to after a successful install.
/// The desktop app re-invokes this with `--update`, and the start-menu /
/// desktop shortcuts can point users back to it. Lives directly under
/// HERMES_HOME so it survives repo checkout deletion (unlike anything under
/// eidolon-agent/).
///
/// Newly staged helpers use the product name inside the selected Eidolon home.
pub fn installer_dest() -> PathBuf {
    let name = if cfg!(target_os = "windows") {
        "eidolon-setup.exe"
    } else {
        "eidolon-setup"
    };
    eidolon_home().join(name)
}

/// Marker the updater writes for the duration of an in-app update and removes
/// when it finishes (see update.rs `UpdateMarkerGuard`). A freshly-launched
/// desktop checks this before spawning its own local backend: spawning one
/// mid-update re-locks the venv shim and triggers `force_kill_other_hermes`,
/// which then kills that legitimate backend in a respawn loop (#50238).
///
/// Lives directly under HERMES_HOME (same rationale as `installer_dest`) so the
/// Electron desktop — which resolves HERMES_HOME identically and pins it into
/// the updater's env — agrees on the exact path.
pub fn update_in_progress_marker() -> PathBuf {
    eidolon_home().join(".eidolon-update-in-progress")
}

/// Copy the currently-running installer binary to `installer_dest()` so it's
/// available for future `--update` runs and shortcut launches.
///
/// No-ops (returns Ok) when the running exe is ALREADY the destination — which
/// is exactly the case during an `--update` run (the desktop launched us FROM
/// that path), where copying onto ourselves would be a Windows sharing
/// violation. Best-effort: a failure here must not fail the install, so the
/// caller logs and continues.
///
/// NOTE: because of that no-op, a user's staged installer is only ever written
/// by a full install/repair. Every later `--update` runs the ORIGINAL binary,
/// so an installer-protocol change can strand the whole installed base on a
/// binary that predates it (see `restage_from_checkout`, which repairs this
/// from the freshly-updated checkout).
pub fn copy_self_to_eidolon_home() -> std::io::Result<()> {
    let src = std::env::current_exe()?;
    let dest = installer_dest();

    // Skip if we're already running from the destination (update re-invocation
    // or a prior copy). canonicalize both so symlinks / 8.3 short paths / case
    // differences don't trick us into a self-copy.
    let same = match (src.canonicalize(), dest.canonicalize()) {
        (Ok(a), Ok(b)) => a == b,
        _ => src == dest,
    };
    if same {
        tracing::info!(?dest, "installer already at destination; skipping self-copy");
        return Ok(());
    }

    if let Some(parent) = dest.parent() {
        std::fs::create_dir_all(parent)?;
    }
    std::fs::copy(&src, &dest)?;
    repair_macos_installer_helper(&dest);
    tracing::info!(?src, ?dest, "copied installer to HERMES_HOME");
    Ok(())
}

#[cfg(target_os = "macos")]
fn repair_macos_installer_helper(path: &Path) {
    // The staged helper may inherit quarantine from the downloaded installer.
    // Desktop later launches this exact file for in-app updates, so make it
    // executable before the update handoff reaches LaunchServices/Gatekeeper.
    let _ = Command::new("/usr/bin/xattr")
        .args(["-cr"])
        .arg(path)
        .status();

    let verify = Command::new("/usr/bin/codesign")
        .arg("--verify")
        .arg(path)
        .status();

    if !matches!(verify, Ok(status) if status.success()) {
        let _ = Command::new("/usr/bin/codesign")
            .args(["--force", "--sign", "-"])
            .arg(path)
            .status();
    }
}

#[cfg(not(target_os = "macos"))]
fn repair_macos_installer_helper(_path: &Path) {}

/// Where the bootstrap-complete marker lives (existence-only for the Rust
/// installer fast path; JSON schema-checked by the Electron app). Per main.ts:
///   const BOOTSTRAP_COMPLETE_MARKER = path.join(ACTIVE_HERMES_ROOT, '.eidolon-bootstrap-complete')
/// We don't always know ACTIVE_HERMES_ROOT until install.ps1 reports it, so
/// this is a probe helper, not a definitive path.
pub fn likely_bootstrap_marker(install_root: &Path) -> PathBuf {
    install_root.join(".eidolon-bootstrap-complete")
}

/// Initializes tracing to bootstrap-installer.log under HERMES_HOME/logs/.
/// Returns a guard that flushes the appender on drop — keep it alive for
/// the lifetime of the process.
pub fn init_logging() -> Option<WorkerGuard> {
    let dir = log_dir();
    if let Err(err) = std::fs::create_dir_all(&dir) {
        // No log dir → log to stderr only. Don't panic; the installer
        // should still be usable on an exotic filesystem.
        eprintln!("[eidolon-setup] could not create log dir {dir:?}: {err}");
        return None;
    }

    let file_appender = tracing_appender::rolling::never(&dir, "bootstrap-installer.log");
    let (non_blocking, guard) = tracing_appender::non_blocking(file_appender);

    let env_filter = tracing_subscriber::EnvFilter::try_from_env("HERMES_BOOTSTRAP_LOG")
        .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("info"));

    tracing_subscriber::fmt()
        .with_env_filter(env_filter)
        .with_writer(non_blocking)
        .with_ansi(false)
        .with_target(true)
        .init();

    Some(guard)
}

// ---------------------------------------------------------------------------
// Tauri commands
// ---------------------------------------------------------------------------

#[tauri::command]
pub fn get_log_path() -> String {
    log_path().to_string_lossy().into_owned()
}

#[tauri::command]
pub fn get_eidolon_home() -> String {
    eidolon_home().to_string_lossy().into_owned()
}

#[tauri::command]
pub fn open_log_dir(app: tauri::AppHandle) -> Result<(), String> {
    use tauri_plugin_opener::OpenerExt;
    let path = log_dir();
    app.opener()
        .open_path(path.to_string_lossy(), None::<&str>)
        .map_err(|e| e.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn default_home_is_isolated_from_hermes_on_every_platform() {
        let user_home = PathBuf::from("fixture-home");
        for unset in [None, Some(String::new()), Some("   ".to_string())] {
            assert_eq!(
                resolve_home(unset, Some(user_home.clone())),
                user_home.join(".eidolon")
            );
        }
        assert_eq!(resolve_home(None, None), PathBuf::from(".eidolon"));
    }

    #[test]
    fn an_explicit_compatibility_home_wins_without_migration() {
        for selected in ["custom-profile", ".hermes"] {
            assert_eq!(
                resolve_home(Some(selected.to_string()), Some(PathBuf::from("fixture-home"))),
                PathBuf::from(selected)
            );
        }
    }
}
