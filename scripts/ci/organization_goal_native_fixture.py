"""Synthetic goal owner fixture. Seeding uses APIs; observation is SQLite read-only."""
from pathlib import Path
import json
import sqlite3


def seed(home):
    import yaml
    from eidolon_cli.organization_config import OrganizationSettings
    from eidolon_cli.organization_store import OrganizationStore
    from eidolon_cli.organization_metadata import save_metadata
    home = Path(home)
    root = home / 'planning-root'
    root.mkdir(exist_ok=True)
    config_path = home / 'config.yaml'
    config = yaml.safe_load(config_path.read_text()) or {}
    config['organization'] = {'read_roots': [str(root)],
        'projects': [{'id': 'planning', 'root': 'root0', 'recipe': None, 'team': 'core'}]}
    config_path.write_text(yaml.safe_dump(config))
    store = OrganizationStore(home / 'organization' / 'state.db', OrganizationSettings.from_config(config))
    save_metadata(store.path, display_name='Goal owner proof', owner_display_name='Fixture owner',
        expected_organization_id=None, expected_revision=None, idempotency_key='goal-native-metadata')
    objective = store.create_objective('Existing bounded planning objective', project_ids=['planning'],
        acceptance_criteria=[{'id': 'summary', 'text': 'A supported summary', 'kind': 'deliverable'}],
        delivery_mode='managed_artifact', idempotency_key='goal-native-objective')
    store.set_objective_paused(objective['id'], True, expected_revision=0, idempotency_key='goal-native-pause')
    return {'objectiveId': objective['id']}


def inspect(home):
    path = Path(home) / 'organization' / 'state.db'
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('BEGIN')
        tables = ('objective_dispatch', 'organization_milestone_revisions', 'organization_milestone_receipts',
            'organization_goal_revisions', 'organization_goal_receipts', 'organization_goal_members',
            'organization_continuation_plans', 'organization_continuation_controls',
            'organization_continuation_receipts', 'organization_continuation_launches')
        return {**{table: [dict(row) for row in conn.execute('SELECT * FROM ' + table + ' ORDER BY rowid')] for table in tables},
            'attempts': conn.execute('SELECT COALESCE(SUM(execution_count),0) FROM requests').fetchone()[0],
            'modelCalls': conn.execute('SELECT COUNT(*) FROM organization_model_calls').fetchone()[0],
            'objectiveCount': conn.execute('SELECT COUNT(*) FROM objectives').fetchone()[0]}


if __name__ == '__main__':
    import sys
    if len(sys.argv) != 3 or sys.argv[1] not in {'seed', 'inspect'}:
        raise SystemExit('Use seed|inspect SYNTHETIC_HOME')
    print(json.dumps((seed if sys.argv[1] == 'seed' else inspect)(sys.argv[2])))
