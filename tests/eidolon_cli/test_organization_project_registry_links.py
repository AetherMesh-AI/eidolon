"""Native link retargeting invalidates the exact project revision reviewed by the owner."""
import subprocess

import pytest

from tests.organization_project_registry_helpers import (
    assert_link_target_is_part_of_reviewed_revision,
)
from tests.organization_project_registry_helpers import (
    profile as profile,
)


@pytest.mark.linux_only
@pytest.mark.parametrize('timing', ['before_review_check', 'after_review_check'])
def test_linux_symlink_retarget_requires_a_new_owner_review(profile, monkeypatch, timing):
    assert_link_target_is_part_of_reviewed_revision(profile, monkeypatch, timing)


@pytest.mark.macos_only
@pytest.mark.parametrize('timing', ['before_review_check', 'after_review_check'])
def test_macos_symlink_retarget_requires_a_new_owner_review(profile, monkeypatch, timing):
    assert_link_target_is_part_of_reviewed_revision(profile, monkeypatch, timing)


@pytest.mark.windows_only
@pytest.mark.parametrize('timing', ['before_review_check', 'after_review_check'])
def test_windows_junction_retarget_requires_a_new_owner_review(profile, monkeypatch, timing):
    alias = profile / 'selected-root'

    def create(target):
        # Native directory junctions need no developer-mode or symlink privilege.
        result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(alias), str(target)],
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, (result.stdout, result.stderr)

    assert_link_target_is_part_of_reviewed_revision(profile, monkeypatch, timing, create=create, remove=alias.rmdir)
