//! Resolve the CLI belonging to the installation selected for update.

use std::path::{Path, PathBuf};

pub(crate) fn is_eidolon_install(install_root: &Path) -> bool {
    let Some(metadata) = std::fs::read(install_root.join("package.json"))
        .ok()
        .and_then(|bytes| serde_json::from_slice::<serde_json::Value>(&bytes).ok())
    else {
        return false;
    };
    let repository = &metadata["repository"];
    let Some(url) = repository.as_str().or_else(|| repository["url"].as_str()) else {
        return false;
    };
    let normalized = url.trim().to_ascii_lowercase();
    let normalized = normalized.strip_prefix("git+").unwrap_or(&normalized);
    let normalized = normalized.trim_end_matches('/');
    let normalized = normalized.strip_suffix(".git").unwrap_or(normalized);
    matches!(normalized,
        "https://github.com/aethermesh-ai/eidolon"
        | "git@github.com:aethermesh-ai/eidolon"
        | "ssh://git@github.com/aethermesh-ai/eidolon")
}

pub(crate) fn install_root(home: &Path) -> PathBuf {
    let current = home.join("eidolon-agent");
    let previous = home.join("hermes-agent");
    if !current.exists() && is_eidolon_install(&previous) {
        previous
    } else {
        current
    }
}

pub(crate) fn needs_namespace_migration(install_root: &Path) -> bool {
    is_eidolon_install(install_root)
        && install_root.join("hermes_cli/main.py").is_file()
        && !install_root.join("eidolon_cli/main.py").is_file()
}

pub(crate) fn venv_cli(install_root: &Path, is_windows: bool) -> PathBuf {
    let (bin_dir, executable) = if is_windows {
        ("Scripts", "eidolon.exe")
    } else {
        ("bin", "eidolon")
    };
    install_root.join("venv").join(bin_dir).join(executable)
}

pub(crate) fn resolve_cli(install_root: &Path, is_windows: bool) -> Option<PathBuf> {
    // An unrelated Hermes installation may still be on PATH. Running that
    // updater with Eidolon's home would mutate the wrong checkout. The staged
    // installer contract uses venv; a missing/broken shim requires repair.
    let shim = venv_cli(install_root, is_windows);
    if shim.is_file() {
        return Some(shim);
    }
    // Only earlier builds of this fork may use the old CLI. Never search PATH.
    let previous = install_root.join("venv")
        .join(if is_windows { "Scripts" } else { "bin" })
        .join(if is_windows { "hermes.exe" } else { "hermes" });
    (is_eidolon_install(install_root) && previous.is_file()).then_some(previous)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn prior_checkout_and_cli_need_verified_fork_metadata() {
        let home = std::env::temp_dir().join(format!(
            "eidolon-prior-install-{}-{}",
            std::process::id(),
            std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos()
        ));
        let previous = home.join("hermes-agent");
        let current = home.join("eidolon-agent");
        let old_cli = previous.join("venv/bin/hermes");
        std::fs::create_dir_all(old_cli.parent().unwrap()).unwrap();
        std::fs::write(&old_cli, b"old fork launcher").unwrap();
        for url in ["https://github.com/NousResearch/hermes-agent.git", "https://github.com/AetherMesh-AI/Eidolon-unrelated.git"] {
            std::fs::write(previous.join("package.json"),
                serde_json::json!({"repository": {"url": url}}).to_string()).unwrap();
            assert_eq!(install_root(&home), current);
            assert_eq!(resolve_cli(&previous, false), None);
        }
        for url in ["git+https://github.com/AetherMesh-AI/Eidolon.git", "git@github.com:AetherMesh-AI/Eidolon.git"] {
            std::fs::write(previous.join("package.json"),
                serde_json::json!({"repository": {"url": url}}).to_string()).unwrap();
            assert_eq!(install_root(&home), previous);
            assert_eq!(resolve_cli(&previous, false), Some(old_cli.clone()));
        }
        std::fs::create_dir_all(previous.join("hermes_cli")).unwrap();
        std::fs::write(previous.join("hermes_cli/main.py"), b"# old fork").unwrap();
        assert!(needs_namespace_migration(&previous));
        std::fs::create_dir_all(previous.join("eidolon_cli")).unwrap();
        std::fs::write(previous.join("eidolon_cli/main.py"), b"# current fork").unwrap();
        assert!(!needs_namespace_migration(&previous));
        let new_cli = venv_cli(&previous, false);
        std::fs::write(&new_cli, b"current fork launcher").unwrap();
        assert_eq!(resolve_cli(&previous, false), Some(new_cli));
        std::fs::create_dir_all(&current).unwrap();
        assert_eq!(install_root(&home), current);
        std::fs::remove_dir_all(home).unwrap();
    }

    #[test]
    fn only_selected_install_cli_is_eligible_even_when_other_install_is_on_path() {
        const CHILD_ROOT: &str = "EIDOLON_TEST_INSTALL_CLI_ROOT";
        if let Some(root) = std::env::var_os(CHILD_ROOT) {
            let root = PathBuf::from(root);
            let install = root.join("eidolon/eidolon-agent");
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
        for executable in ["eidolon", "eidolon.exe", "hermes", "hermes.exe"] {
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
