//! Desktop artifact names are product metadata, not the legacy `hermes` CLI.
//! Keep lookup inside the selected installation and prefer current Eidolon
//! artifacts over older builds left in that same release tree, after checking
//! architecture compatibility with the running installer.

use std::fs::File;
use std::io::{Read, Seek, SeekFrom};
use std::path::{Path, PathBuf};

#[derive(Clone, Copy)]
pub(crate) enum DesktopPlatform {
    Windows,
    Macos,
    Linux,
}

impl DesktopPlatform {
    pub(crate) fn current() -> Self {
        if cfg!(target_os = "windows") {
            Self::Windows
        } else if cfg!(target_os = "macos") {
            Self::Macos
        } else {
            Self::Linux
        }
    }
}

fn executable_candidates(install_root: &Path, platform: DesktopPlatform) -> Vec<PathBuf> {
    let release = install_root.join("apps").join("desktop").join("release");
    let relative: &[&str] = match platform {
        DesktopPlatform::Windows => &[
            "win-unpacked/Eidolon.exe",
            "win-arm64-unpacked/Eidolon.exe",
            "win-ia32-unpacked/Eidolon.exe",
            "win-unpacked/Hermes.exe",
            "win-arm64-unpacked/Hermes.exe",
            "win-ia32-unpacked/Hermes.exe",
        ],
        DesktopPlatform::Macos => &[
            "mac/Eidolon.app/Contents/MacOS/Eidolon",
            "mac-arm64/Eidolon.app/Contents/MacOS/Eidolon",
            "mac-universal/Eidolon.app/Contents/MacOS/Eidolon",
            "mac/Hermes.app/Contents/MacOS/Hermes",
            "mac-arm64/Hermes.app/Contents/MacOS/Hermes",
            "mac-universal/Hermes.app/Contents/MacOS/Hermes",
        ],
        DesktopPlatform::Linux => &[
            "linux-unpacked/Eidolon",
            "linux-arm64-unpacked/Eidolon",
            "linux-ia32-unpacked/Eidolon",
            "linux-unpacked/hermes",
            "linux-arm64-unpacked/hermes",
            "linux-ia32-unpacked/hermes",
            "linux-unpacked/Hermes",
            "linux-arm64-unpacked/Hermes",
            "linux-ia32-unpacked/Hermes",
        ],
    };
    relative.iter().map(|path| release.join(path)).collect()
}

pub(crate) fn resolve_desktop_executable(
    install_root: &Path,
    platform: DesktopPlatform,
) -> Option<PathBuf> {
    resolve_desktop_for_arch(
        install_root,
        platform,
        std::env::consts::ARCH,
        native_windows_machine(),
    )
}

fn resolve_desktop_for_arch(
    install_root: &Path,
    platform: DesktopPlatform,
    architecture: &str,
    native_windows_machine: Option<u16>,
) -> Option<PathBuf> {
    executable_candidates(install_root, platform)
        .into_iter()
        .find(|path| {
            path.is_file()
                && matches_architecture(path, platform, architecture, native_windows_machine)
        })
}

fn matches_architecture(
    path: &Path,
    platform: DesktopPlatform,
    architecture: &str,
    native_windows_machine: Option<u16>,
) -> bool {
    match platform {
        DesktopPlatform::Windows => {
            // Both the running installer target and the native host machine
            // are known to work. In particular, an x64 installer on Windows
            // ARM64 must still launch a newly built native ARM64 desktop.
            // Do not infer any additional emulation capabilities.
            pe_machine(path).is_some_and(|machine| {
                Some(machine) == windows_machine_for_arch(architecture)
                    || Some(machine) == native_windows_machine
            })
        }
        DesktopPlatform::Macos => {
            let directory = path.ancestors().nth(4).and_then(Path::file_name);
            let expected = match architecture {
                "x86_64" => "mac",
                "aarch64" => "mac-arm64",
                _ => return false,
            };
            directory.is_some_and(|name| name == expected || name == "mac-universal")
        }
        DesktopPlatform::Linux => {
            let directory = path.parent().and_then(Path::file_name);
            let expected = match architecture {
                "x86_64" => "linux-unpacked",
                "aarch64" => "linux-arm64-unpacked",
                "x86" => "linux-ia32-unpacked",
                _ => return false,
            };
            directory.is_some_and(|name| name == expected)
        }
    }
}

fn windows_machine_for_arch(architecture: &str) -> Option<u16> {
    match architecture.to_ascii_lowercase().as_str() {
        "x86_64" | "amd64" | "x64" => Some(0x8664),
        "aarch64" | "arm64" => Some(0xAA64),
        "x86" | "i386" | "i686" => Some(0x014C),
        _ => None,
    }
}

#[cfg(windows)]
fn native_windows_machine() -> Option<u16> {
    use std::ffi::c_void;

    // Resolve this API dynamically so older Windows can still use the
    // environment fallback. GetNativeSystemInfo can report the emulated
    // machine; IsWow64Process2 explicitly reports the OS-native machine.
    #[link(name = "kernel32")]
    extern "system" {
        fn GetModuleHandleW(name: *const u16) -> *mut c_void;
        fn GetProcAddress(module: *mut c_void, name: *const u8)
            -> Option<unsafe extern "system" fn() -> isize>;
        fn GetCurrentProcess() -> *mut c_void;
    }
    type IsWow64Process2 = unsafe extern "system" fn(*mut c_void, *mut u16, *mut u16) -> i32;
    let module_name: Vec<u16> = "kernel32.dll\0".encode_utf16().collect();
    // The module lives for the process lifetime. The resolved symbol has the
    // documented IsWow64Process2 signature, and both output pointers are valid.
    unsafe {
        let module = GetModuleHandleW(module_name.as_ptr());
        if !module.is_null() {
            if let Some(symbol) = GetProcAddress(module, b"IsWow64Process2\0".as_ptr()) {
                let probe: IsWow64Process2 = std::mem::transmute(symbol);
                let mut process = 0u16;
                let mut native = 0u16;
                if probe(GetCurrentProcess(), &mut process, &mut native) != 0
                    && matches!(native, 0x8664 | 0xAA64 | 0x014C)
                {
                    return Some(native);
                }
            }
        }
    }
    ["PROCESSOR_ARCHITEW6432", "PROCESSOR_ARCHITECTURE"]
        .iter()
        .filter_map(|key| std::env::var(key).ok())
        .find_map(|arch| windows_machine_for_arch(&arch))
}

#[cfg(not(windows))]
fn native_windows_machine() -> Option<u16> {
    None
}

/// Read the machine from a structurally complete PE header/section table.
/// This is a loadability filter, not a signature or executable authenticity check.
fn pe_machine(path: &Path) -> Option<u16> {
    let mut file = File::open(path).ok()?;
    let size = file.metadata().ok()?.len();
    if size < 512 {
        return None;
    }
    let mut dos = [0u8; 64];
    file.read_exact(&mut dos).ok()?;
    if &dos[..2] != b"MZ" {
        return None;
    }
    let offset = u32::from_le_bytes(dos[0x3c..0x40].try_into().ok()?) as u64;
    if offset == 0 || offset + 24 > size {
        return None;
    }
    file.seek(SeekFrom::Start(offset)).ok()?;
    let mut header = [0u8; 24];
    file.read_exact(&mut header).ok()?;
    if &header[..4] != b"PE\0\0" {
        return None;
    }
    let sections = u16::from_le_bytes([header[6], header[7]]) as u64;
    let optional_size = u16::from_le_bytes([header[20], header[21]]) as u64;
    let section_table = offset + 24 + optional_size;
    if section_table + sections * 40 > size {
        return None;
    }
    file.seek(SeekFrom::Start(section_table)).ok()?;
    for _ in 0..sections {
        let mut section = [0u8; 40];
        file.read_exact(&mut section).ok()?;
        let raw_size = u32::from_le_bytes(section[16..20].try_into().ok()?) as u64;
        let raw_offset = u32::from_le_bytes(section[20..24].try_into().ok()?) as u64;
        if raw_offset + raw_size > size {
            return None;
        }
    }
    Some(u16::from_le_bytes([header[4], header[5]]))
}

pub(crate) fn desktop_payload_paths(
    install_root: &Path,
    platform: DesktopPlatform,
) -> Vec<PathBuf> {
    let mut payloads = Vec::new();
    for executable in executable_candidates(install_root, platform) {
        let payload = match platform {
            DesktopPlatform::Macos => executable
                .parent()
                .and_then(Path::parent)
                .map(|contents| contents.join("Resources").join("app.asar")),
            _ => executable.parent().map(|dir| dir.join("resources").join("app.asar")),
        };
        if let Some(payload) = payload {
            if !payloads.contains(&payload) {
                payloads.push(payload);
            }
        }
    }
    payloads
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temporary_root(label: &str) -> PathBuf {
        let root = std::env::temp_dir().join(format!(
            "eidolon-artifacts-{label}-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::create_dir_all(&root).unwrap();
        root
    }

    fn write_executable(root: &Path, relative: &str) -> PathBuf {
        let path = root.join("apps/desktop/release").join(relative);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        if path.extension().is_some_and(|ext| ext == "exe") {
            write_pe(&path, 0x8664);
        } else {
            std::fs::write(&path, b"fixture executable").unwrap();
        }
        path
    }

    fn write_pe(path: &Path, machine: u16) {
        let mut bytes = vec![0u8; 1024];
        bytes[..2].copy_from_slice(b"MZ");
        bytes[0x3c..0x40].copy_from_slice(&0x80u32.to_le_bytes());
        bytes[0x80..0x84].copy_from_slice(b"PE\0\0");
        bytes[0x84..0x86].copy_from_slice(&machine.to_le_bytes());
        bytes[0x86..0x88].copy_from_slice(&1u16.to_le_bytes());
        bytes[0xa8..0xac].copy_from_slice(&512u32.to_le_bytes());
        bytes[0xac..0xb0].copy_from_slice(&512u32.to_le_bytes());
        std::fs::write(path, bytes).unwrap();
    }

    #[test]
    fn new_artifacts_win_and_legacy_fallback_stays_inside_the_selected_root() {
        for (platform, current, legacy) in [
            (DesktopPlatform::Windows, "win-unpacked/Eidolon.exe", "win-unpacked/Hermes.exe"),
            (DesktopPlatform::Macos, "mac/Eidolon.app/Contents/MacOS/Eidolon", "mac/Hermes.app/Contents/MacOS/Hermes"),
            (DesktopPlatform::Linux, "linux-unpacked/Eidolon", "linux-unpacked/hermes"),
        ] {
            let root = temporary_root("precedence");
            let unrelated = root.join("unrelated-install");
            write_executable(&unrelated, legacy);
            assert_eq!(resolve_desktop_for_arch(&root, platform, "x86_64", None), None);

            let previous = write_executable(&root, legacy);
            assert_eq!(resolve_desktop_for_arch(&root, platform, "x86_64", None), Some(previous.clone()));
            let preferred = write_executable(&root, current);
            assert_eq!(resolve_desktop_for_arch(&root, platform, "x86_64", None), Some(preferred.clone()));
            std::fs::remove_file(&preferred).unwrap();
            // An incomplete build directory is not a launchable binary.
            std::fs::create_dir(&preferred).unwrap();
            assert_eq!(resolve_desktop_for_arch(&root, platform, "x86_64", None), Some(previous));
            std::fs::remove_dir_all(root).unwrap();
        }
    }

    #[test]
    fn architecture_precedes_brand_and_invalid_windows_headers_are_rejected() {
        for (architecture, machine, other_machine) in [
            ("x86_64", 0x8664, 0xAA64),
            ("aarch64", 0xAA64, 0x8664),
            ("x86", 0x014C, 0x8664),
        ] {
            let root = temporary_root("windows-machine");
            let previous = write_executable(&root, "win-unpacked/Hermes.exe");
            let current = write_executable(&root, "win-arm64-unpacked/Eidolon.exe");
            write_pe(&previous, machine);
            write_pe(&current, other_machine);
            let resolve = || resolve_desktop_for_arch(&root, DesktopPlatform::Windows, architecture, None);
            assert_eq!(resolve(), Some(previous.clone()));
            write_pe(&current, machine);
            assert_eq!(resolve(), Some(current.clone()));
            // Folder names cannot override the actual PE machine, and a broken
            // current-name file cannot hide a loadable legacy fallback.
            std::fs::write(&current, b"incomplete download").unwrap();
            assert_eq!(resolve(), Some(previous.clone()));
            std::fs::remove_file(previous).unwrap();
            assert_eq!(resolve(), None);
            write_pe(&current, other_machine);
            assert_eq!(resolve(), None);
            // The production entry point uses the real compiler target.
            if architecture == std::env::consts::ARCH {
                write_pe(&current, machine);
                assert_eq!(resolve_desktop_executable(&root, DesktopPlatform::Windows), Some(current));
            }
            std::fs::remove_dir_all(root).unwrap();
        }
        // Windows-on-ARM: the installer itself is x64 under emulation, while
        // install.ps1 builds the native ARM64 desktop. Both machines can run.
        let root = temporary_root("windows-arm-host");
        let previous = write_executable(&root, "win-unpacked/Hermes.exe");
        let current = write_executable(&root, "win-arm64-unpacked/Eidolon.exe");
        write_pe(&current, 0xAA64);
        assert_eq!(resolve_desktop_for_arch(&root, DesktopPlatform::Windows, "x86_64", Some(0x8664)), Some(previous.clone()));
        assert_eq!(resolve_desktop_for_arch(&root, DesktopPlatform::Windows, "x86_64", Some(0xAA64)), Some(current.clone()));
        std::fs::remove_file(&current).unwrap();
        assert_eq!(resolve_desktop_for_arch(&root, DesktopPlatform::Windows, "x86_64", Some(0xAA64)), Some(previous));
        std::fs::remove_dir_all(root).unwrap();
        for (platform, wrong, legacy, native) in [
            (DesktopPlatform::Linux, "linux-arm64-unpacked/Eidolon", "linux-unpacked/hermes", "linux-unpacked/Eidolon"),
            (DesktopPlatform::Macos, "mac-arm64/Eidolon.app/Contents/MacOS/Eidolon", "mac/Hermes.app/Contents/MacOS/Hermes", "mac-universal/Eidolon.app/Contents/MacOS/Eidolon"),
        ] {
            let root = temporary_root("unix-architecture");
            write_executable(&root, wrong);
            assert_eq!(resolve_desktop_for_arch(&root, platform, "x86_64", None), None);
            let previous = write_executable(&root, legacy);
            assert_eq!(resolve_desktop_for_arch(&root, platform, "x86_64", None), Some(previous));
            let preferred = write_executable(&root, native);
            assert_eq!(resolve_desktop_for_arch(&root, platform, "x86_64", None), Some(preferred));
            std::fs::remove_dir_all(root).unwrap();
        }
    }

    #[test]
    fn lock_probe_paths_include_current_and_legacy_bundle_payloads_without_duplicates() {
        let root = Path::new("fixture-install");
        let mac = desktop_payload_paths(root, DesktopPlatform::Macos);
        for name in ["Eidolon", "Hermes"] {
            assert!(mac.contains(&root.join(format!(
                "apps/desktop/release/mac-arm64/{name}.app/Contents/Resources/app.asar"
            ))));
        }
        let windows = desktop_payload_paths(root, DesktopPlatform::Windows);
        assert!(windows.contains(&root.join("apps/desktop/release/win-unpacked/resources/app.asar")));
        let mut unique = windows.clone();
        unique.sort();
        unique.dedup();
        assert_eq!(windows.len(), unique.len());
        assert!(desktop_payload_paths(root, DesktopPlatform::Linux)
            .contains(&root.join("apps/desktop/release/linux-arm64-unpacked/resources/app.asar")));
    }
}
