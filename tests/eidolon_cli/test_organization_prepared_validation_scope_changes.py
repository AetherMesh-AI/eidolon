"""Selected mandatory profile checks cannot disappear during later planning."""
import json

import pytest

from eidolon_cli.organization_prepared_validation_objectives import validate_selected_prepared_checks
from eidolon_cli.organization_projects import objective_projects
from tests.eidolon_cli.test_organization_prepared_validation_stage_runtime import (  # noqa: F401
    running, owner, prepared, folder_profile,
)

pytestmark = pytest.mark.linux_only


def test_prepared_planner_preserves_the_pair_and_rejects_unsupported_additions(running):
    with running.store._write() as conn:
        running.store._merge_required_checks(conn, running.objective, [])
        checks = json.loads(conn.execute('SELECT required_checks FROM objective_control WHERE objective_id=?',
                                         (running.objective,)).fetchone()[0])
        assert {'project_tests', 'project_typecheck'} <= set(checks)
        projects = objective_projects(conn, running.objective)
        for changed in ([], ['project_tests'], ['project_typecheck'],
                        ['project_tests', 'project_typecheck', 'source_integration'],
                        ['project_tests', 'project_typecheck', 'project_build']):
            with pytest.raises(ValueError, match='mandatory'):
                validate_selected_prepared_checks(conn, running.objective, projects, changed)
        for unsupported in ('project_build', 'source_integration'):
            with pytest.raises(ValueError, match='mandatory'):
                running.store._merge_required_checks(conn, running.objective, [unsupported])
        assert json.loads(conn.execute('SELECT required_checks FROM objective_control WHERE objective_id=?',
                                       (running.objective,)).fetchone()[0]) == checks


def test_prepared_replacement_requires_fresh_explicit_owner_selection(running):
    from eidolon_cli.organization_replacement import replacement_preview
    with running.store._connect() as conn:
        with pytest.raises(ValueError, match='new explicitly reviewed objective selection'):
            replacement_preview(conn, running.store.settings, running.objective, running.store.allowed_owner_resolutions)
