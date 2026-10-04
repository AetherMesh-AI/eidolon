---
sidebar_position: 2
title: "Installation"
description: "Install Eidolon on Linux, macOS, WSL2, native Windows, or Android via Termux"
---

# Installation

:::caution Experimental build
Consult the [current README](https://github.com/AetherMesh-AI/eidolon#readme) and [security policy](https://github.com/AetherMesh-AI/eidolon/blob/main/SECURITY.md). Platform and update behavior described here must be verified for the exact build; prior upstream release claims do not establish Eidolon acceptance.
:::


Eidolon is experimental. Use the current source or a release with explicit verification notes, and review the installer before running it. Native installation and recovery must be checked on your target OS.

:::tip Platform Support
For the full platform support matrix (which OSes, distribution methods, and
platform-gated features are supported), see **[Platform Support](./platform-support.md)**.
:::

## Quick Install
### With the Eidolon Desktop installer on macOS or Windows (release-specific verification required)
To easily install the command-line and desktop applications, [download the Eidolon Desktop installer](https://github.com/AetherMesh-AI/eidolon/releases) from our website and run it.

### Without Eidolon Desktop:
For a command-line only install without Eidolon Desktop, run:

#### Linux / macOS / WSL2 / Android (Termux)
```bash
curl -fsSL https://raw.githubusercontent.com/AetherMesh-AI/eidolon/main/scripts/install.sh | bash
```

#### Windows (native)

Run in powershell:
```powershell
iex (irm https://raw.githubusercontent.com/AetherMesh-AI/eidolon/main/scripts/install.ps1)
```

If you want to install & run Eidolon Desktop after a command-line only install, simply run
```bash
eidolon desktop
```

### What the Installer Does

The installer attempts the configured setup stages for dependencies (Python, Node.js, ripgrep, ffmpeg), the repo clone, virtual environment, global `eidolon` command setup, and LLM provider configuration. A completed installer is not proof of a working provider connection; verify a conversation separately.

#### Install Layout

Where the installer puts things depends on whether you're installing as a normal user or as root:

| Installer                              | Code lives at                  | `eidolon` binary                         | Data directory                       |
| -------------------------------------- | ------------------------------ | --------------------------------------- | ------------------------------------ |
| Per-user (git installer)               | `~/.eidolon/eidolon-agent/`      | `~/.local/bin/eidolon` (symlink)         | `~/.eidolon/`                         |
| Root-mode (`sudo curl … \| sudo bash`) | `/usr/local/lib/eidolon-agent/` | `/usr/local/bin/eidolon`                 | `/root/.eidolon/` (or `$HERMES_HOME`) |

The root-mode **FHS layout** (`/usr/local/lib/…`, `/usr/local/bin/eidolon`) matches where other system-wide developer tools land on Linux. It's useful for shared-machine deployments where one system install should serve every user. Per-user config (auth, skills, sessions) still lives under each user's `~/.eidolon/` or explicit `HERMES_HOME`.

### After Installation

Reload your shell and start chatting:

```bash
source ~/.bashrc   # or: source ~/.zshrc
eidolon             # Start chatting!
```

To reconfigure individual settings later, use the dedicated commands:

```bash
eidolon model          # Choose your LLM provider and model
eidolon tools          # Configure which tools are enabled
eidolon gateway setup  # Set up messaging platforms
eidolon config set     # Set individual config values
eidolon config get     # Inspect individual config values
eidolon setup          # Or run the full setup wizard to configure everything at once
```

:::note Optional Nous Portal
Nous Portal remains a third-party provider option. If you choose it, `eidolon setup --portal` starts its authentication and provider setup. Other providers and custom endpoints remain available; no subscription or hosted service is required by the Eidolon rebrand.
:::

:::tip Already running Eidolon on another machine?
You don't need to rebuild your setup from scratch. Restore a full backup with `eidolon import` (see [Exporting Eidolon to another machine](/reference/faq#exporting-eidolon-to-another-machine)), or bring over a single agent with `eidolon profile import` (see [Moving a single profile to another machine](/reference/faq#moving-a-single-profile-to-another-machine)). Note that a profile export excludes credentials by design, so an export alone is not a full backup — [`eidolon backup` vs `eidolon profile export`](/reference/faq#eidolon-backup-vs-eidolon-profile-export) explains which to use.
:::

---

## Prerequisites

**Installer:** On non-Windows platforms, the only prerequisite is **Git**. On Linux, also make sure `curl` and `xz-utils` are available (the installer downloads Node.js as a `.tar.xz` archive). The desktop app additionally requires `g++` (or `build-essential` on Debian/Ubuntu) to compile native modules. The installer automatically handles everything else:

- **uv** (fast Python package manager)
- **Python 3.11** (via uv, no sudo needed)
- **Node.js v26** (for browser automation and WhatsApp bridge; existing system Node 22.22+, 24.11+, or 26+ is used as-is)
- **ripgrep** (fast file search)
- **ffmpeg** (audio format conversion for TTS)

:::info
You do **not** need to install Python, Node.js, ripgrep, or ffmpeg manually. The installer detects what's missing and installs it for you. Just make sure `git` is available (`git --version`). On Linux, ensure `curl` and `xz-utils` are installed (`sudo apt install curl xz-utils` on Debian/Ubuntu). For the desktop app, also install `build-essential` (`sudo apt install build-essential`).
:::

:::tip Nix users
Nix is **no longer an explicitly supported install path** (best-effort only). If you already use Nix (on NixOS, macOS, or Linux), there's a dedicated setup path with a Nix flake, declarative NixOS module, and optional container mode. See the **[Nix & NixOS Setup](./nix-setup.md)** guide.
:::

---

## Manual / Developer Installation

If you want to clone the repo and install from source — for contributing, running from a specific branch, or having full control over the virtual environment — see the [Development Setup](../developer-guide/contributing.md#development-setup) section in the Contributing guide.

---

## Non-Sudo / System Service User Installs

Running Eidolon as a dedicated unprivileged user (e.g. a `eidolon` systemd service account, or any user without `sudo` access) is supported. The only thing on the install path that genuinely needs root is Playwright's `--with-deps` step, which `apt`-installs shared libraries (`libnss3`, `libxkbcommon`, etc.) used by Chromium. The installer detects whether sudo is available and gracefully degrades when it isn't — it will install the Chromium binary into the service user's own Playwright cache and print the exact command an administrator needs to run separately.

**Recommended split (Debian/Ubuntu):**

1. **One time, as an admin user with sudo**, install the system libraries Chromium needs:
   ```bash
   sudo npx playwright install-deps chromium
   ```
   (You can run this from anywhere — `npx` will fetch Playwright on the fly.)

2. **As the unprivileged service user**, run the regular installer. It will detect the missing sudo, skip `--with-deps`, and install Chromium into the user's local Playwright cache:
   ```bash
   curl -fsSL https://raw.githubusercontent.com/AetherMesh-AI/eidolon/main/scripts/install.sh | bash
   ```

   If you want to skip the Playwright step entirely — for example because you're running headless and don't need browser automation — pass `--skip-browser`:
   ```bash
   curl -fsSL https://raw.githubusercontent.com/AetherMesh-AI/eidolon/main/scripts/install.sh | bash -s -- --skip-browser
   ```

   The installer also pre-installs [`cua-driver`](../user-guide/features/computer-use.md) so the Computer Use toolset works the moment you enable it; pass `--skip-computer-use` to opt out (it will then install on demand when you enable the tool).

3. **Make `eidolon` available to the service user's shells.** The installer writes the launcher to `~/.local/bin/eidolon`. System service accounts often have a minimal PATH that doesn't include `~/.local/bin`. Either add it to the user's environment, or symlink the launcher into a system location:
   ```bash
   # Option A — add to the service user's profile
   echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc

   # Option B — symlink system-wide (run as an admin)
   sudo ln -s /home/hermes/.hermes/hermes-agent/venv/bin/eidolon /usr/local/bin/eidolon
   ```

4. **Verify:** `eidolon doctor` should now run cleanly. If you get `ModuleNotFoundError: No module named 'dotenv'`, you're invoking the repo source `eidolon` file (`~/.eidolon/eidolon-agent/hermes`) with system Python instead of the venv launcher (`~/.eidolon/eidolon-agent/venv/bin/eidolon`) — fix step 3.

5. **Running the messaging gateway from this account?** A user-level service stops at logout and does not start at boot until you enable lingering for the service user:

   ```bash
   sudo loginctl enable-linger <service-user>
   ```

   See [Messaging Gateway](/user-guide/messaging/) for the service setup itself.

The same pattern works on Arch (the installer uses pacman with the same sudo-detection logic), Fedora/RHEL, and openSUSE — those distros don't support `--with-deps` at all, so an administrator always installs the system libraries separately. The relevant `dnf`/`zypper` commands are printed by the installer.

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `hermes: command not found` | Reload your shell (`source ~/.bashrc`) or check PATH |
| `API key not set` | Run `eidolon model` to configure your provider, or `eidolon config set OPENROUTER_API_KEY your_key` |
| Missing config after update | Run `eidolon config check` then `eidolon config migrate` |

For more diagnostics, run `eidolon doctor` — it will tell you exactly what's missing and how to fix it.

### Symlinked home directories and external storage

Eidolon supports a symlinked `HERMES_HOME` and symlinked home subdirectories,
including `hooks`, `skills`, `sessions`, and `logs`. During home initialization,
existing directory links are preserved, and permissions on linked directories
(and descendants such as `logs/curator`) are left to their owner.

If a link target is missing, inaccessible, or not a directory, initialization
stops with a storage error naming the path and link target. Eidolon does **not**
replace the link or create its missing target: doing so could write data onto
the local disk while an external or NAS volume is unmounted. Check the reported
link, restore the mount or correct its target, and verify access permissions
before retrying. For a deliberately new dotfiles target, create it yourself only
after confirming the intended storage is available.

`eidolon doctor` reports these failures as storage problems, not invalid YAML.
Keep your existing `config.yaml`; running `eidolon setup` is not the repair for an
unavailable directory. This is a directory-availability check, not a mount monitor:
an existing directory cannot establish that the intended volume is mounted.

## Install method auto-detection

Eidolon auto-detects whether it was installed via the git installer, Docker, or NixOS, and `eidolon update` prints the matching update command for that path. There's no env var to set — the detection is based on the install layout (`~/.eidolon/eidolon-agent/` checkout, Docker image stamp, or Nix store path). `eidolon doctor` also surfaces the detected method under its environment summary.
