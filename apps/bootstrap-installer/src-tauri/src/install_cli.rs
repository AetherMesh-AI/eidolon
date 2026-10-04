//! Resolve the CLI belonging to the installation selected for update.

use std::path::{Path, PathBuf};

pub(crate) fn venv_cli(install_root: &Path, is_windows: bool) -> PathBuf {
    let (bin_dir, executable) = if is_windows {
        ("Scripts", "hermes.exe")
    } else {
        ("bin", "hermes")
    };
    install_root.join("venv").join(bin_dir).join(executable)
}

pub(crate) fn resolve_cli(install_root: &Path, is_windows: bool) -> Option<PathBuf> {
    // An unrelated Hermes installation may still be on PATH. Running that
    // updater with Eidolon's home would mutate the wrong checkout. The staged
    // installer contract uses venv; a missing/broken shim requires repair.
    let shim = venv_cli(install_root, is_windows);
    shim.is_file().then_some(shim)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_selected_install_cli_is_eligible_even_when_other_install_is_on_path() {
        const CHILD_ROOT: &str = "EIDOLON_TEST_INSTALL_CLI_ROOT";
        if let Some(root) = std::env::var_os(CHILD_ROOT) {
            let root = PathBuf::from(root);
            let install = root.join("eidolon/hermes-agent");
            for is_windows in [false, true] {
                assert_eq!(resolve_cli(&install, is_windows), None);
                let own = venv_cli(&install, is_windows);
                std::fs::create_dir_all(&own).unwrap();
                assert_eq!(resolve_cli(&install, is_windows), None);
                std::fs::remove_dir(&own).unwrap();
                std::fs::write(&own, b"selected-install fixture").unwrap();
                assert_eq!(resolve_cli(&install, is_windows), Some(own.clone()));
                std::fs::remove_file(own).unwrap();
                assert_eq!(resolve_cli(&install, is_windows), None);
            }
            std::fs::write(root.join("fixture-exercised"), b"passed").unwrap();
            return;
        }

        let root = std::env::temp_dir().join(format!(
            "eidolon-cli-isolation-{}-{}",
            std::process::id(),
            std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos()
        ));
        let unrelated = root.join("hermes/hermes-agent/venv/bin");
        std::fs::create_dir_all(&unrelated).unwrap();
        for executable in ["hermes", "hermes.exe"] {
            std::fs::write(unrelated.join(executable), b"unrelated-install fixture").unwrap();
        }
        // Isolate PATH changes in a subprocess, so parallel tests never observe
        // another test's environment. No fixture CLI is executed.
        // Test-harness names omit the crate prefix; retain the enclosing module
        // when compiled as part of Tauri instead of a standalone test binary.
        let (_, module) = module_path!().split_once("::").unwrap();
        let filter = format!(
            "{module}::only_selected_install_cli_is_eligible_even_when_other_install_is_on_path"
        );
        let output = std::process::Command::new(std::env::current_exe().unwrap())
            .args(["--exact", &filter, "--nocapture"])
            .env(CHILD_ROOT, &root)
            .env("PATH", &unrelated)
            .output().unwrap();
        let exercised = root.join("fixture-exercised").is_file();
        std::fs::remove_dir_all(root).unwrap();
        assert!(output.status.success(), "{}{}", String::from_utf8_lossy(&output.stdout), String::from_utf8_lossy(&output.stderr));
        assert!(exercised, "the child test must execute the filesystem assertions");
    }
}
