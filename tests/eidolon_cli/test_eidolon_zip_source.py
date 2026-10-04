"""The Windows archive recovery route must never select upstream Eidolon source."""

from types import SimpleNamespace
import zipfile

import pytest

from eidolon_cli import main as hermes_main
from eidolon_cli import update_cmd, update_cmd_zip
from eidolon_cli.eidolon_update_policy import UPDATE_BRANCH, UPDATE_REPO


class DownloadReached(Exception):
    """Stop before dependency installation; archive swapping is covered separately."""


@pytest.mark.parametrize("branch", [None, "main", "feature/other"])
def test_zip_update_uses_only_the_canonical_source_branch(tmp_path, monkeypatch, branch):
    monkeypatch.setattr(hermes_main, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(hermes_main, "_capture_active_tool_dependencies", lambda: [])
    monkeypatch.setattr(update_cmd, "_read_project_version", lambda: "fixture")
    requests = []

    def capture_download(selected_branch, url):
        requests.append((selected_branch, url))
        raise DownloadReached

    monkeypatch.setattr(update_cmd_zip, "_download_and_swap_zip", capture_download)
    if branch in (None, UPDATE_BRANCH):
        with pytest.raises(DownloadReached):
            update_cmd_zip._update_via_zip(SimpleNamespace(branch=branch))
        assert requests == [
            (UPDATE_BRANCH, f"https://github.com/{UPDATE_REPO}/archive/refs/heads/{UPDATE_BRANCH}.zip")
        ]
    else:
        with pytest.raises(SystemExit) as exc:
            update_cmd_zip._update_via_zip(SimpleNamespace(branch=branch))
        assert exc.value.code == 1
        assert requests == []


@pytest.mark.parametrize("archive_root", ["eidolon-main", "Eidolon-main", "eidolon-agent-main", "other-project-main"])
def test_archive_identity_is_checked_before_replacing_local_files(tmp_path, monkeypatch, archive_root):
    install = tmp_path / "install"
    install.mkdir()
    readme = install / "README.md"
    readme.write_text("existing Eidolon source", encoding="utf-8")
    monkeypatch.setattr(hermes_main, "PROJECT_ROOT", install)
    requests = []

    def local_archive(url, destination):
        requests.append(url)
        with zipfile.ZipFile(destination, "w") as archive:
            archive.writestr(f"{archive_root}/README.md", "new Eidolon source")

    monkeypatch.setattr("urllib.request.urlretrieve", local_archive)
    url = f"https://github.com/{UPDATE_REPO}/archive/refs/heads/{UPDATE_BRANCH}.zip"
    if archive_root.casefold() == "eidolon-main":
        update_cmd_zip._download_and_swap_zip(UPDATE_BRANCH, url)
        assert readme.read_text(encoding="utf-8") == "new Eidolon source"
    else:
        with pytest.raises(SystemExit) as exc:
            update_cmd_zip._download_and_swap_zip(UPDATE_BRANCH, url)
        assert exc.value.code == 1
        assert readme.read_text(encoding="utf-8") == "existing Eidolon source"
    assert requests == [url]
    assert not list(install.glob("*.eidolon-update-*"))
