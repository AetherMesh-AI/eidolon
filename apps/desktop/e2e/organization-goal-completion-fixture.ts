/** Public-API setup and read-only evidence for the real scheduler acceptance proof. */
import { execFileSync } from 'node:child_process'
import path from 'node:path'

import { setupCompletionFixture } from './organization-completion-fixture'

const root = path.resolve(import.meta.dirname, '../../..')
const python = path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
export const goalTitles = ['First finite release recommendation', 'Dependent finite release recommendation']
export interface Launch { template_id: string; objective_id: string; scope_sha256: string; created: number }
export interface Ledger {
  launches: Launch[]
  calls: Array<{ id: string; objective_id: string; input_limit: number; output_limit: number }>
  usage: Array<{ objective_id: string }>
  acceptances: Array<{ objective_id: string; agent_id: string; approved: number; created: number }>
  deliverables: Array<{ objective_id: string; author_id: string; sha256: string; content: string }>
}

export async function setupGoalCompletionFixture() {
  let seedId = ''

  const run = (home: string, source: string) => JSON.parse(execFileSync(python, ['-c', source, home], {
    cwd: root, encoding: 'utf8', env: { PATH: process.env.PATH, HOME: path.dirname(home), USERPROFILE: path.dirname(home), HERMES_HOME: home }
  }))

  const running = await setupCompletionFixture(false, { finiteGoal: {
    titles: goalTitles,
    prepareHome: home => {
      seedId = run(home, `
import json,sys,yaml
from pathlib import Path
from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_store import OrganizationStore
from eidolon_cli.organization_metadata import save_metadata
home=Path(sys.argv[1])
store=OrganizationStore(home/'organization'/'state.db',OrganizationSettings.from_config(yaml.safe_load((home/'config.yaml').read_text())))
save_metadata(store.path,display_name='Finite goal acceptance',owner_display_name='Synthetic owner',expected_organization_id=None,expected_revision=None,idempotency_key='goal-owner')
seed=store.create_objective('Paused goal membership anchor',delivery_mode='managed_artifact',idempotency_key='goal-anchor')
store.set_objective_paused(seed['id'],True,expected_revision=0,idempotency_key='pause-anchor')
print(json.dumps(seed['id']))
`) as string
    }
  } })

  const ledger = () => run(running.fixture.sandbox.hermesHome, `
import json,sqlite3,sys
from pathlib import Path
with sqlite3.connect((Path(sys.argv[1])/'organization'/'state.db').as_uri()+'?mode=ro',uri=True) as conn:
 conn.row_factory=sqlite3.Row
 conn.execute('BEGIN')
 tables={'launches':'organization_continuation_launches','calls':'organization_model_calls','usage':'objective_usage','acceptances':'objective_acceptances','deliverables':'objective_deliverables'}
 print(json.dumps({key:[dict(row) for row in conn.execute('SELECT * FROM '+table+' ORDER BY rowid')] for key,table in tables.items()}))
`) as Ledger

  return { ...running, seedId, ledger }
}
