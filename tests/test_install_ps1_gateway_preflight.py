"""Execute Windows registration preflight against real task XML/VBS/CMD files.

The Scheduled Tasks boundary is replaced; all ownership decisions, wrapper
traversal, and installer entry points execute in PowerShell. No OS tasks change.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from xml.sax.saxutils import escape

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/install_windows_gateway"
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")
pytestmark = pytest.mark.skipif(not POWERSHELL, reason="PowerShell runtime required")
SELECTED_HOME = r"C:\Users\Case Owner\.eidolon"
SELECTED_INSTALL = SELECTED_HOME + r"\hermes-agent"


def _ps(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _wrapper(tmp_path: Path, *, name="gateway.vbs", home=SELECTED_HOME,
             install=SELECTED_INSTALL, module="hermes_cli.main", python=None,
             venv=None, pythonpath=None, profile="") -> Path:
    path = tmp_path / name
    text = (FIXTURES / ("gateway.cmd" if name.endswith(".cmd") else "gateway.vbs")).read_text()
    values = {"HOME": home, "VENV": venv if venv is not None else install + r"\venv",
              "PYTHONPATH": pythonpath if pythonpath is not None else install,
              "PYTHON": python if python is not None else install + r"\venv\Scripts\python.exe",
              "MODULE": module, "PROFILE": f"--profile {profile} " if profile else ""}
    for key, value in values.items():
        text = text.replace(f"@{key}@", value)
    path.write_text(text)
    return path


def _task(tmp_path: Path, wrapper: Path, *, name=r"\Hermes_Gateway", enabled=True,
          command="wscript.exe", arguments=None) -> dict:
    path = tmp_path / f"task-{len(list(tmp_path.glob('task-*.xml')))}.xml"
    text = (FIXTURES / "task.xml").read_text()
    values = {"COMMAND": command, "ARGUMENTS": arguments or f'//B //Nologo "{wrapper}"',
              "ENABLED": str(enabled).lower()}
    for key, value in values.items():
        text = text.replace(f"@{key}@", escape(value))
    path.write_text(text, encoding="utf-16")
    return {"Name": name, "Xml": str(path)}


def _run(tmp_path: Path, *, tasks=(), body="", startup=None) -> dict:
    task_file = tmp_path / "tasks.json"
    task_file.write_text(json.dumps(tasks))
    script = tmp_path / "exercise.ps1"
    script.write_text(f"""
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$tokens = $null; $parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile({_ps(ROOT / 'scripts/install.ps1')}, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) {{ throw ($parseErrors | Out-String) }}
$ast.FindAll({{ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and ($node.Name -like '*InstallerGateway*' -or $node.Name -in @('Install-Repository', 'Install-Venv')) }}, $false) | ForEach-Object {{ . ([scriptblock]::Create($_.Extent.Text)) }}
$HermesHome = {_ps(SELECTED_HOME)}
$InstallDir = {_ps(SELECTED_INSTALL)}
$NoVenv = $false
$env:OS = 'Windows_NT'
$script:tasks = @(Get-Content -Raw -LiteralPath {_ps(task_file)} | ConvertFrom-Json)
$script:calls = [System.Collections.Generic.List[object]]::new()
$script:messages = [System.Collections.Generic.List[string]]::new()
function Write-Info {{ param($value) $script:messages.Add([string]$value) }}
function Write-Warn {{ param($value) $script:messages.Add([string]$value) }}
function schtasks {{
    $callArgs = @($args); $script:calls.Add($callArgs)
    $global:LASTEXITCODE = 0
    if ($callArgs[0] -ne '/Query') {{ return }}
    if ($callArgs -contains '/XML') {{
        $name = $callArgs[[array]::IndexOf($callArgs, '/TN') + 1]
        $task = $script:tasks | Where-Object {{ $_.Name -eq $name }}
        if ($task) {{ Get-Content -LiteralPath $task.Xml }}
        return
    }}
    foreach ($task in $script:tasks) {{ '"' + $task.Name + '","N/A","Bereit"' }}
}}
$startup = {_ps(startup or tmp_path / 'empty-startup')}
{body}
@{{ result = $result; calls = @($script:calls.ToArray()); messages = @($script:messages.ToArray()) }} | ConvertTo-Json -Depth 12 -Compress
""")
    env = os.environ | {"XDG_CACHE_HOME": str(tmp_path / "cache"), "XDG_CONFIG_HOME": str(tmp_path / "config"),
                        "XDG_DATA_HOME": str(tmp_path / "data")}
    completed = subprocess.run([POWERSHELL, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(script)],
                               text=True, capture_output=True, env=env, timeout=30)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return json.loads(completed.stdout)


@pytest.mark.parametrize("suffix", ["vbs", "cmd"])
@pytest.mark.parametrize("profile", ["", "work"])
def test_registered_legacy_wrapper_belongs_to_selected_home_and_install(tmp_path, suffix, profile):
    home = SELECTED_HOME + (rf"\profiles\{profile}" if profile else "")
    wrapper = _wrapper(tmp_path, name=f"gateway.{suffix}", home=home, profile=profile)
    task = _task(tmp_path, wrapper, command="wscript.exe" if suffix == "vbs" else "cmd.exe",
                 arguments=f'//B //Nologo "{wrapper}"' if suffix == "vbs" else f'/d /c "{wrapper}"')
    result = _run(tmp_path, tasks=[task], body="$result = @(Get-InstallerGatewayRegistrations -StartupDirectory $startup)")
    assert result["result"] == [{"Kind": "Task", "Name": task["Name"], "Home": home,
                                  "Module": "hermes_cli.main", "Enabled": True, "ActionCount": 1}]
    assert all(call[0] == "/Query" for call in result["calls"])


@pytest.mark.parametrize("changes", [
    {"home": r"C:\Users\Case Owner\.hermes"},
    {"home": SELECTED_HOME + "-other"},
    {"home": SELECTED_HOME + r"\profiles\work\unrelated"},
    {"install": r"C:\Users\Case Owner\.hermes\hermes-agent"},
    {"install": SELECTED_INSTALL + "-other"},
    {"module": "hermes_cli.main.unrelated"},
    {"home": ""},
])
def test_foreign_or_unproven_registration_is_untouched(tmp_path, changes):
    wrapper = _wrapper(tmp_path, **changes)
    result = _run(tmp_path, tasks=[_task(tmp_path, wrapper)], body="$result = @(Get-InstallerGatewayRegistrations -StartupDirectory $startup)")
    assert result["result"] == []
    assert all(call[0] == "/Query" for call in result["calls"])


@pytest.mark.parametrize("proof", ["interpreter", "venv", "pythonpath"])
def test_each_complete_install_path_can_prove_wrapper_scope(tmp_path, proof):
    foreign = r"D:\Other"
    values = {"python": foreign + r"\python.exe", "venv": foreign, "pythonpath": foreign}
    values[{"interpreter": "python", "venv": "venv", "pythonpath": "pythonpath"}[proof]] = {
        "interpreter": SELECTED_INSTALL + r"\venv\Scripts\pythonw.exe",
        "venv": SELECTED_INSTALL + r"\venv", "pythonpath": SELECTED_INSTALL,
    }[proof]
    wrapper = _wrapper(tmp_path, **values)
    result = _run(tmp_path, tasks=[_task(tmp_path, wrapper)], body="$result = @(Get-InstallerGatewayRegistrations -StartupDirectory $startup)")
    assert len(result["result"]) == 1


def test_startup_vbs_follows_executed_service_wrapper(tmp_path):
    wrapper = _wrapper(tmp_path)
    startup = tmp_path / "Startup"
    startup.mkdir()
    (startup / "Hermes_Gateway.vbs").write_text((FIXTURES / "startup.vbs").read_text().replace("@TARGET@", str(wrapper)))
    _wrapper(startup, name="Hermes_Gateway_other.cmd", home=r"C:\Users\Case Owner\.hermes")
    result = _run(tmp_path, startup=startup, body="$result = @(Get-InstallerGatewayRegistrations -StartupDirectory $startup)")
    assert [entry["Name"] for entry in result["result"]] == [str(startup / "Hermes_Gateway.vbs")]


def test_wrapper_in_comment_or_unexecuted_task_argument_is_not_proof(tmp_path):
    wrapper = _wrapper(tmp_path)
    task = _task(tmp_path, wrapper, command="cmd.exe", arguments=f'/c echo "{wrapper}"')
    result = _run(tmp_path, tasks=[task], body="$result = @(Get-InstallerGatewayRegistrations -StartupDirectory $startup)")
    assert result["result"] == []


@pytest.mark.parametrize("entrypoint", ["Install-Repository", "Install-Venv"])
def test_each_mutating_stage_refuses_legacy_task_before_any_mutation(tmp_path, entrypoint):
    wrapper = _wrapper(tmp_path, home=SELECTED_HOME + r"\profiles\work", profile="work")
    task = _task(tmp_path, wrapper, name=r"\Eidolon\Hermes_Gateway_work", enabled=False)
    result = _run(tmp_path, tasks=[task], body=f"""
try {{ {entrypoint}; $result = 'unexpected success' }} catch {{ $result = $_.Exception.Message }}
""")
    assert "Retire the selected install legacy gateway" in result["result"]
    assert all(call[0] == "/Query" for call in result["calls"])
    messages = "\n".join(result["messages"])
    assert "schtasks /Change /TN '\\Eidolon\\Hermes_Gateway_work' /DISABLE" in messages
    assert "schtasks /End /TN '\\Eidolon\\Hermes_Gateway_work'" in messages
    assert "schtasks /Delete /TN '\\Eidolon\\Hermes_Gateway_work' /F" in messages
    assert ".task.xml' -Encoding Unicode" in messages
    assert f"$env:HERMES_HOME = '{SELECTED_HOME}\\profiles\\work'" in messages
    assert "eidolon.exe' gateway install" in messages
    assert "eidolon.exe' gateway start" in messages


def test_suspend_only_enabled_owned_canonical_tasks(tmp_path):
    own = _wrapper(tmp_path, name="own.vbs", module="eidolon_cli.main")
    foreign = _wrapper(tmp_path, name="foreign.vbs", module="eidolon_cli.main", install=r"D:\Other")
    legacy = _wrapper(tmp_path, name="legacy.vbs")
    tasks = [
        _task(tmp_path, own, name=r"\Eidolon_Gateway"),
        _task(tmp_path, own, name=r"\Eidolon_Gateway_disabled", enabled=False),
        _task(tmp_path, foreign, name=r"\Eidolon_Gateway_foreign"),
        _task(tmp_path, legacy, name=r"\Hermes_Gateway"),
        _task(tmp_path, own, name=r"\Hermes_Gateway_updated"),
    ]
    result = _run(tmp_path, tasks=tasks, body="$result = @(Suspend-InstallerGatewayTasks)")
    assert result["result"] == [r"\Eidolon_Gateway"]
    assert [call for call in result["calls"] if call[0] != "/Query"] == [
        ["/End", "/TN", r"\Eidolon_Gateway"],
        ["/Change", "/TN", r"\Eidolon_Gateway", "/DISABLE"],
    ]


def test_startup_refusal_prints_backup_and_selected_home_reinstall(tmp_path):
    startup = tmp_path / "Startup"
    startup.mkdir()
    wrapper = _wrapper(startup, name="Hermes_Gateway.cmd")
    result = _run(tmp_path, startup=startup, body="""
$script:readRegistrations = ${function:Get-InstallerGatewayRegistrations}
function Get-InstallerGatewayRegistrations {
    param([switch]$RequireInspection)
    & $script:readRegistrations -StartupDirectory $startup -RequireInspection:$RequireInspection
}
try { Assert-NoLegacyInstallerGateway; $result = 'unexpected success' } catch { $result = $_.Exception.Message }
""")
    assert "Retire the selected install legacy gateway" in result["result"]
    assert wrapper.exists()
    messages = "\n".join(result["messages"])
    assert f"Move-Item -LiteralPath '{wrapper}' -Destination '{wrapper}.backup-" in messages
    assert "hermes_cli.main gateway stop" in messages
    assert "eidolon.exe' gateway install" in messages


@pytest.mark.parametrize("failure", ["query", "xml", "wrapper"])
def test_inspection_failure_cannot_authorize_replacing_legacy_runtime(tmp_path, failure):
    wrapper = _wrapper(tmp_path)
    task = _task(tmp_path, wrapper)
    if failure == "xml":
        Path(task["Xml"]).write_text("not valid XML")
    setup = {
        "query": "function schtasks { $global:LASTEXITCODE = 1 }",
        "xml": "",
        "wrapper": "function Read-InstallerGatewayWrapper { throw 'Access denied to wrapper' }",
    }[failure]
    result = _run(tmp_path, tasks=[task], body=setup + """
try { $null = Get-InstallerGatewayRegistrations -StartupDirectory $startup -RequireInspection; $result = 'unexpected success' }
catch { $result = $_.Exception.Message }
""")
    assert "Could not inspect gateway scheduled tasks before changing the legacy install" in result["result"]
    assert all(call[0] == "/Query" for call in result["calls"])


def test_multi_action_task_is_not_automatically_disabled(tmp_path):
    own = _wrapper(tmp_path, module="eidolon_cli.main")
    task = _task(tmp_path, own, name=r"\Eidolon_Gateway")
    path = Path(task["Xml"])
    xml = path.read_text(encoding="utf-16").replace("</Actions>", "<Exec><Command>other.exe</Command></Exec></Actions>")
    path.write_text(xml, encoding="utf-16")
    result = _run(tmp_path, tasks=[task], body="$result = @(Suspend-InstallerGatewayTasks)")
    assert result["result"] == []
    assert all(call[0] == "/Query" for call in result["calls"])


@pytest.mark.parametrize("artifact", ["hermes_cli", "venv/Scripts/hermes.exe", None])
def test_strict_inspection_tracks_actual_selected_legacy_runtime(tmp_path, artifact):
    install = tmp_path / "selected-install"
    install.mkdir()
    if artifact == "hermes_cli":
        (install / artifact).mkdir()
    elif artifact:
        (install / artifact).parent.mkdir(parents=True)
        (install / artifact).write_text("old launcher")
    result = _run(tmp_path, body=f"""
$InstallDir = {_ps(install)}
function schtasks {{ $global:LASTEXITCODE = 1 }}
try {{ Assert-NoLegacyInstallerGateway; $result = 'allowed' }} catch {{ $result = $_.Exception.Message }}
""")
    if artifact:
        assert "Could not inspect gateway scheduled tasks before changing the legacy install" in result["result"]
        assert (install / artifact).exists()
    else:
        assert result["result"] == "allowed"


@pytest.mark.parametrize("alias_home", [False, True])
def test_directory_alias_still_identifies_selected_legacy_gateway(tmp_path, alias_home):
    home = tmp_path / "real-home"
    install = home / "hermes-agent"
    scripts = install / "venv/Scripts"
    scripts.mkdir(parents=True)
    python = scripts / "python.exe"
    python.write_text("selected interpreter")
    alias = tmp_path / "selected-alias"
    alias.symlink_to(home if alias_home else install, target_is_directory=True)
    selected_home = alias if alias_home else home
    selected_install = alias / "hermes-agent" if alias_home else alias
    wrapper = _wrapper(tmp_path, home=str(home), install=str(install), python=str(python), venv="", pythonpath="")
    result = _run(tmp_path, tasks=[_task(tmp_path, wrapper)], body=f"""
$HermesHome = {_ps(selected_home)}
$InstallDir = {_ps(selected_install)}
$result = @(Get-InstallerGatewayRegistrations -StartupDirectory $startup -RequireInspection)
""")
    assert len(result["result"]) == 1
    assert result["result"][0]["Module"] == "hermes_cli.main"


def test_shared_interpreter_symlink_does_not_merge_unrelated_venvs(tmp_path):
    system_python = tmp_path / "system-python.exe"
    system_python.write_text("shared Python")
    selected = tmp_path / "selected"
    foreign = tmp_path / "foreign"
    for install in [selected, foreign]:
        scripts = install / "venv/Scripts"
        scripts.mkdir(parents=True)
        (scripts / "python.exe").symlink_to(system_python)
    wrapper = _wrapper(tmp_path, install=str(foreign), python=str(foreign / "venv/Scripts/python.exe"), venv="", pythonpath="")
    result = _run(tmp_path, tasks=[_task(tmp_path, wrapper)], body=f"""
$InstallDir = {_ps(selected)}
$result = @(Get-InstallerGatewayRegistrations -StartupDirectory $startup -RequireInspection)
""")
    assert result["result"] == []


def test_selected_venv_directory_alias_resolves_without_following_python_file(tmp_path):
    install = tmp_path / "selected-install"
    install.mkdir()
    actual_venv = tmp_path / "managed-venv"
    scripts = actual_venv / "Scripts"
    scripts.mkdir(parents=True)
    python = scripts / "python.exe"
    python.write_text("selected interpreter")
    (install / "venv").symlink_to(actual_venv, target_is_directory=True)
    wrapper = _wrapper(tmp_path, install=str(install), python=str(python), venv="", pythonpath="")
    result = _run(tmp_path, tasks=[_task(tmp_path, wrapper)], body=f"""
$InstallDir = {_ps(install)}
$result = @(Get-InstallerGatewayRegistrations -StartupDirectory $startup -RequireInspection)
""")
    assert len(result["result"]) == 1
