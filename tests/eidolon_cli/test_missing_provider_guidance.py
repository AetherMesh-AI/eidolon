"""Missing-provider recovery guidance follows the active credential home."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.mark.parametrize("relative_home", [".eidolon", ".eidolon/profiles/alpha", ".eidolon/profiles/beta"])
def test_missing_provider_guidance_uses_current_home(tmp_path, monkeypatch, relative_home):
    synthetic_home = tmp_path / "home"
    synthetic_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: synthetic_home)
    active_home = synthetic_home / relative_home
    active_home.mkdir(parents=True)
    with patch.dict(os.environ, {"HOME": str(synthetic_home), "HERMES_HOME": str(active_home),
                                 "AWS_EC2_METADATA_DISABLED": "true"}, clear=True):
        from eidolon_cli.auth import AuthError, resolve_provider

        with pytest.raises(AuthError) as error:
            resolve_provider("auto")
        assert error.value.code == "no_provider_configured"
        assert f"in ~/{relative_home}/.env." in str(error.value)

        # Resolve again after changing the override: guidance must not cache a profile.
        outside_home = tmp_path / "explicit-home"
        outside_home.mkdir()
        os.environ["HERMES_HOME"] = str(outside_home)
        with pytest.raises(AuthError) as changed:
            resolve_provider("auto")
        assert changed.value.code == "no_provider_configured"
        assert f"in {outside_home}/.env." in str(changed.value)
