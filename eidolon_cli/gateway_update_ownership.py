"""Effective service-manager authority for updater fleet mutations."""

import shlex
import subprocess

_CAPTURE_TEXT = {"capture_output": True, "text": True, "encoding": "utf-8", "errors": "replace"}


def _update_systemd_service_identity(scope_cmd: list, name: str):
    """Snapshot effective manager configuration and its exact live incarnation.

    Disk fragments alone do not describe a loaded unit (drop-ins and daemon-reload).
    Unknown or extended execution environments are deliberately left to the operator.
    """
    from eidolon_cli.gateway import _update_service_definition_details, update_service_process_is_owned
    try:
        import psutil
        import re
        details = _update_service_definition_details(name, manager="systemd", system="--user" not in scope_cmd)
        if details is None:
            return None
        properties = ("MainPID", "ExecStart", "Environment", "WorkingDirectory", "FragmentPath",
                      "EnvironmentFiles", "PassEnvironment", "UnsetEnvironment", "ExecStartPre",
                      "ExecStartPost", "ExecStop", "ExecStopPost", "RootDirectory", "RootImage")
        result = subprocess.run(scope_cmd + ["show", name, "--property=" + ",".join(properties)],
                                timeout=5, **_CAPTURE_TEXT)
        if result.returncode != 0:
            return None
        fields = {}
        for line in result.stdout.splitlines():
            key, eq, value = line.partition("=")
            if not eq or key in fields:
                return None
            fields[key] = value
        if set(fields) != set(properties) or any(fields[key] for key in properties[5:]):
            return None
        pid = int(fields["MainPID"])
        if pid <= 0:
            return None
        # systemctl's struct printer is not shell syntax. Accept only its single
        # unescaped argv form; ambiguous quoting or multiple commands fail closed.
        match = re.fullmatch(r"\{ path=([^;{}]+) ; argv\[\]=([^;{}]+) ; ignore_errors=no ; [^{}]*\}", fields["ExecStart"])
        if not match or any(c in match[2] for c in "\\\"'"):
            return None
        if match[1].strip() != details["args"][0] or match[2].split() != details["args"]:
            return None
        env = dict(item.split("=", 1) for item in shlex.split(fields["Environment"]))
        if (env.get("HERMES_HOME") != details["home"] or env.get("VIRTUAL_ENV") != details["venv"]
                or any(key in env for key in ("PYTHONPATH", "PYTHONHOME", "LD_PRELOAD", "LD_LIBRARY_PATH"))
                or any(key.startswith("DYLD_") for key in env)
                or fields["WorkingDirectory"] != details["cwd"]
                or fields["FragmentPath"] != details["definition_path"]):
            return None
        created = psutil.Process(pid).create_time()
        if not update_service_process_is_owned(pid, name, manager="systemd", system="--user" not in scope_cmd):
            return None
        if psutil.Process(pid).create_time() != created:
            return None
        return pid, created, tuple(sorted(fields.items()))
    except Exception:
        return None


def _loaded_launchd_definition_matches(output: str, details: dict) -> bool:
    """Compare explicit loaded launch fields, refusing ambiguous print formats."""
    import re
    try:
        def scalar(key):
            values = re.findall(r"^\s*" + re.escape(key) + r" = (.+)$", output, re.M)
            return values[0] if len(values) == 1 else None
        def block(key):
            # Braces inside an argument are literal payload. Only a standalone
            # closing brace at the opening field's indentation terminates it.
            starts = list(re.finditer(r"^([ \t]*)" + re.escape(key) + r" = \{[ \t]*$", output, re.M))
            if len(starts) != 1:
                return None
            start = starts[0]
            tail = output[start.end() + 1:]
            end = re.search(r"^" + re.escape(start[1]) + r"\}[ \t]*$", tail, re.M)
            if end is None:
                return None
            lines = tail[:end.start()].splitlines()
            # Every payload line must be nested beneath this field. A sibling
            # field before its terminator means the dump is ambiguous/truncated.
            if any(line and (not line.startswith(start[1])
                   or len(line) <= len(start[1]) or not line[len(start[1])].isspace()) for line in lines):
                return None
            return "\n".join(lines)
        args_block = block("arguments")
        env_block = block("environment")
        if args_block is None or env_block is None:
            return False
        argument_lines = args_block.splitlines()
        expected = details["args"]
        if not argument_lines or not expected or not argument_lines[0].endswith(expected[0]):
            return False
        prefix = argument_lines[0][:-len(expected[0])]
        if not prefix or not prefix.isspace() or argument_lines != [prefix + arg for arg in expected]:
            return False
        args = expected
        env = {}
        for line in env_block.splitlines():
            key, sep, value = line.strip().partition(" => ")
            if not sep or key in env:
                return False
            env[key] = value
        if re.search(r"(?:PYTHONPATH|PYTHONHOME|LD_PRELOAD|LD_LIBRARY_PATH|DYLD_\w+)\s*=>", output):
            return False
        return (args == details["args"] and scalar("program") == details["args"][0]
                and scalar("path") == details["definition_path"]
                and scalar("working directory") == details["cwd"]
                and env.get("HERMES_HOME") == details["home"]
                and env.get("VIRTUAL_ENV") == details["venv"])
    except Exception:
        return False


def _update_launchd_service_identity(label: str):
    """Retain loaded definition plus exact native incarnation for every matching domain."""
    import os
    import psutil
    from eidolon_cli.gateway import (_update_service_definition_details,
        update_service_process_is_owned, _parse_launchd_pid_from_print_output)
    try:
        details = _update_service_definition_details(label, manager="launchd")
        if details is None:
            return None
        identities = []
        for domain in (f"gui/{os.getuid()}", f"user/{os.getuid()}"):
            result = subprocess.run(["launchctl", "print", f"{domain}/{label}"], timeout=5, **_CAPTURE_TEXT)
            if result.returncode != 0:
                detail = str(result.stderr or "").lower()
                if "could not find service" in detail and label.lower() in detail:
                    continue
                return None
            pid = _parse_launchd_pid_from_print_output(result.stdout)
            if not pid or not _loaded_launchd_definition_matches(result.stdout, details):
                return None
            created = psutil.Process(pid).create_time()
            if not update_service_process_is_owned(pid, label, manager="launchd"):
                return None
            if psutil.Process(pid).create_time() != created:
                return None
            identities.append((domain, pid, created, tuple(details["args"]), details["home"]))
        return tuple(identities) if identities else None
    except Exception:
        return None
