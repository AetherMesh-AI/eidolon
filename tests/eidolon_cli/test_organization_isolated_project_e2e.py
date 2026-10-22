"""Reuse the real project pipeline; never replace test/isolation receipts with mocks."""
import pytest
from tests.eidolon_cli.test_organization_workspaces import origin  # noqa: F401
from tests.eidolon_cli.test_organization_project_execution_e2e import _native_project_loop


@pytest.mark.linux_only
@pytest.mark.parametrize('recipe', ['python_unittest', 'python_pytest'])
def test_isolated_edit_review_test_integrate_exact_bytes(origin, tmp_path, monkeypatch, recipe):
    # Unlike an availability probe, acceptance requires the native runner to pass.
    monkeypatch.setenv('EIDOLON_REQUIRE_PROJECT_SANDBOX', '1')
    _native_project_loop(tmp_path, monkeypatch, recipe=recipe, isolated_origin=origin)


@pytest.mark.macos_only
def test_isolated_edit_reaches_test_refusal_without_source_publication(origin, tmp_path, monkeypatch):
    _native_project_loop(tmp_path, monkeypatch, isolated_origin=origin)
