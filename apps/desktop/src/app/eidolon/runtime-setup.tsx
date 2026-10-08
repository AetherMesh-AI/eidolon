import { Link } from 'react-router'

import { Button } from '@/components/ui/button'
import { useI18n } from '@/i18n/context'
import { setSettingsScope } from '@/store/settings-scope'

import type { OrganizationAgent, OrganizationSnapshot } from './types'

// Model-using requests from the organization executor; control-only identities
// and deterministic staffing never select a provider.
const modelRequests = new Set([
  'work.draft',
  'work.analyze',
  'work.inspect',
  'work.edit',
  'request.decompose',
  'request.plan',
  'request.review',
  'request.test_review',
  'request.integrate',
  'request.accept',
  'request.question',
  'request.decision'
])

interface RuntimeSetupProps {
  snapshot: OrganizationSnapshot
}

export function inheritsProfileModel(snapshot: OrganizationSnapshot, agent: OrganizationAgent): boolean {
  return (
    snapshot.runtime?.setup?.version === 1 &&
    agent.persistent === true &&
    ['Executive', 'Manager', 'Worker'].includes(agent.role) &&
    ['active', 'available', 'disabled'].includes(agent.lifecycle ?? '') &&
    agent.capabilities.some(capability => modelRequests.has(capability)) &&
    agent.provider === null &&
    agent.model === null
  )
}

export function RuntimeSetup({ snapshot }: RuntimeSetupProps) {
  const { t } = useI18n()
  const copy = t.organizationRoster
  const runtime = snapshot.runtime
  const setup = runtime?.setup?.version === 1 ? runtime.setup : undefined
  const current = snapshot.connection?.state === 'ready'
  // A dedicated backend may call its profile "default" while the desktop's
  // verified owner route uses another logical profile. Settings uses that route.
  const profile = snapshot.connection?.ownerRoute?.profile ?? runtime?.profile

  const executives = snapshot.agents.filter(
    agent =>
      agent.persistent &&
      agent.role === 'Executive' &&
      agent.lifecycle === 'active' &&
      agent.capabilities.includes('request.accept')
  )

  const leaders = snapshot.agents.some(
    agent =>
      agent.persistent &&
      agent.role === 'Manager' &&
      agent.lifecycle === 'active' &&
      executives.some(executive => executive.id === agent.managerId) &&
      ['request.plan', 'request.integrate'].every(capability => agent.capabilities.includes(capability))
  )

  const workers = snapshot.agents.some(
    agent =>
      agent.persistent &&
      agent.role === 'Worker' &&
      ['active', 'available'].includes(agent.lifecycle ?? '') &&
      agent.capabilities.some(capability => runtime?.capabilities.includes(capability))
  )

  const grant = (value: boolean | undefined) =>
    value === undefined ? t.organizationRuntime.notReported : value ? copy.setupConfigured : copy.setupNotEnabled

  return (
    <section aria-label={copy.setupTitle} className="eid-runtime-capabilities">
      <h2>{copy.setupTitle}</h2>
      {runtime && !current && <p>{copy.setupStale}</p>}
      {!runtime ? (
        <p>{copy.setupWaiting}</p>
      ) : !setup ? (
        <p>{copy.setupUnavailable}</p>
      ) : (
        <>
          <p>
            <strong>
              {setup.provider.status === 'warning' ? copy.setupProviderWarning : copy.setupProviderUnchecked}
            </strong>
          </p>
          {setup.provider.blockers.includes('codex_app_server') && <p>{copy.setupCodexWarning}</p>}
          <p className="eid-note">{copy.setupNote}</p>
          <details>
            <summary>{copy.setupDetails}</summary>
            <dl className="eid-runtime-facts">
              <dt>{copy.setupInherited}</dt>
              <dd>{setup.provider.inheritedMembers}</dd>
              <dt>{copy.setupOverrides}</dt>
              <dd>{setup.provider.overriddenMembers}</dd>
              <dt>{copy.roster}</dt>
              <dd>{leaders && workers ? copy.setupRosterPresent : copy.setupRosterMissing}</dd>
              {executives.length > 0 && <>
                <dt>{t.organizationWork.packagePlanningMode}</dt>
                <dd><ul>{executives.map(executive => <li key={executive.id}>{executive.name}: {executive.capabilities.includes('request.decompose') ? t.organizationWork.packageExecutiveMode : t.organizationWork.packageLegacyMode}</li>)}</ul></dd>
              </>}
              <dt>{copy.setupProjects}</dt>
              <dd>{runtime?.availableProjects?.length ?? t.organizationRuntime.notReported}</dd>
              <dt>{t.organizationRuntime.readGrant}</dt>
              <dd>{grant(runtime?.readFileEnabled)}</dd>
              <dt>{t.organizationRuntime.edits.grant}</dt>
              <dd>{grant(runtime?.workspaceApplyEnabled)}</dd>
              <dt>{t.organizationWork.projectExecutionGrant}</dt>
              <dd>{grant(runtime?.projectExecutionEnabled)}</dd>
              <dt>{t.organizationWork.sourceIntegrationGrant}</dt>
              <dd>{grant(runtime?.sourceIntegrationEnabled)}</dd>
              <dt>{copy.setupBackground}</dt>
              <dd>{grant(setup.backgroundOptIn)}</dd>
            </dl>
            <p className="eid-note">{copy.setupCountsNote}</p>
            <p className="eid-note">{copy.setupRosterNote}</p>
            <p className="eid-note">{copy.setupProjectNote}</p>
            <p className="eid-note">{copy.setupBackgroundNote}</p>
          </details>
        </>
      )}
      <div className="eid-inline">
        {current && profile ? (
          <>
            <Button asChild size="sm" variant="link">
              <Link onClick={() => setSettingsScope(profile)} to="/settings?tab=config:model">
                {copy.setupModelSettings}
              </Link>
            </Button>
            <Button asChild size="sm" variant="link">
              <Link onClick={() => setSettingsScope(profile)} to="/settings?tab=providers">
                {copy.setupProviderSettings}
              </Link>
            </Button>
          </>
        ) : (
          <p className="eid-note">{copy.setupScopeUnavailable}</p>
        )}
        <Button asChild size="sm" variant="link">
          <Link to="/organization">{copy.setupRosterLink}</Link>
        </Button>
      </div>
    </section>
  )
}
