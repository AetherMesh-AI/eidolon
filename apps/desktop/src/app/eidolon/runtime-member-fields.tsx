import { controlVariants } from '@/components/ui/control'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { useI18n } from '@/i18n/context'

import { leaderRole, memberCapabilities, memberRoles, responseAuthority } from './runtime-management'
import type { OrganizationManagement, OrganizationMemberConfiguration, OrganizationSnapshot } from './types'

interface OrganizationMemberFieldsProps {
  member: OrganizationMemberConfiguration
  roster: OrganizationMemberConfiguration[]
  management: OrganizationManagement
  snapshot: OrganizationSnapshot
  existing: boolean
  updateMember(values: Partial<OrganizationMemberConfiguration>): void
}

export function OrganizationMemberFields({
  member,
  roster,
  management,
  snapshot,
  existing,
  updateMember
}: OrganizationMemberFieldsProps) {
  const { t } = useI18n()
  const copy = t.organizationRoster
  const roles = { Executive: copy.executive, Manager: copy.manager, Worker: copy.worker }

  const leaders = [
    ...new Map(
      [...snapshot.agents, ...roster]
        .filter(agent => agent.role === leaderRole[member.role])
        .map(agent => [agent.id, agent])
    ).values()
  ]

  const toggle = (field: 'capabilities' | 'authority' | 'tool_grants', value: string, checked: boolean) => {
    updateMember({
      [field]: checked ? [...member[field], value] : member[field].filter(item => item !== value),
      ...(field === 'authority' && value === 'staff.manage' && !checked ? { managed_teams: [] } : {})
    })
  }

  return (
    <>
      <label>
        {copy.identityId}
        <Input
          disabled={existing}
          maxLength={64}
          onChange={event => updateMember({ id: event.target.value })}
          pattern="[a-z][a-z0-9_-]{0,63}"
          required
          value={member.id}
        />
      </label>
      <label>
        {copy.name}
        <Input
          maxLength={100}
          onChange={event => updateMember({ name: event.target.value })}
          required
          value={member.name}
        />
      </label>
      <label>
        {copy.role}
        <select
          className={controlVariants()}
          onChange={event => {
            const role = event.target.value as OrganizationMemberConfiguration['role']
            const manager = [...roster, ...snapshot.agents].find(agent => agent.role === leaderRole[role])
            updateMember({
              role,
              manager_id: manager?.id || '',
              capabilities: member.capabilities.filter(value => memberCapabilities(role, management).includes(value)),
              tool_grants: role === 'Worker' ? member.tool_grants : [],
              authority: member.authority.filter(value => value !== 'staff.manage' || role !== 'Worker'),
              managed_teams: role === 'Worker' ? [] : member.managed_teams
            })
          }}
          value={member.role}
        >
          {memberRoles.map(role => (
            <option key={role} value={role}>
              {roles[role]}
            </option>
          ))}
        </select>
      </label>
      <label>
        {copy.reportsTo}
        <select
          className={controlVariants()}
          onChange={event => updateMember({ manager_id: event.target.value })}
          required
          value={member.manager_id}
        >
          <option value="">{t.organizationWork.notRecorded}</option>
          {leaders.map(agent => (
            <option key={agent.id} value={agent.id}>
              {agent.name}
            </option>
          ))}
        </select>
      </label>
      <label>
        {t.organizationWork.team}
        <Input
          maxLength={64}
          onChange={event => updateMember({ team: event.target.value })}
          required
          value={member.team}
        />
      </label>
      <label>
        {copy.scope}
        <Textarea
          maxLength={3000}
          onChange={event => updateMember({ scope: event.target.value })}
          rows={2}
          value={member.scope || ''}
        />
      </label>
      <label>
        {copy.purpose}
        <Textarea
          maxLength={3000}
          onChange={event => updateMember({ purpose: event.target.value })}
          rows={3}
          value={member.purpose}
        />
      </label>
      <label>
        {copy.responsibilities}
        <Textarea
          onChange={event =>
            updateMember({ responsibilities: event.target.value ? event.target.value.split('\n') : [] })
          }
          rows={3}
          value={member.responsibilities.join('\n')}
        />
      </label>
      <label className="eid-inline">
        <input
          checked={member.enabled}
          onChange={event => updateMember({ enabled: event.target.checked })}
          type="checkbox"
        />
        {copy.enabled}
      </label>
      <fieldset>
        <legend>{copy.acceptedTypes}</legend>
        {memberCapabilities(member.role, management).map(value => (
          <label className="eid-inline" key={value}>
            <input
              checked={member.capabilities.includes(value)}
              onChange={event => toggle('capabilities', value, event.target.checked)}
              type="checkbox"
            />
            {value}
          </label>
        ))}
      </fieldset>
      <fieldset>
        <legend>{copy.authority}</legend>
        {[...responseAuthority, ...(member.role === 'Worker' ? [] : ['staff.manage'])].map(value => (
          <label className="eid-inline" key={value}>
            <input
              checked={member.authority.includes(value)}
              onChange={event => toggle('authority', value, event.target.checked)}
              type="checkbox"
            />
            {value}
          </label>
        ))}
      </fieldset>
      {member.role !== 'Worker' && (
        <>
          <label>
            {copy.managedTeams}
            <Input
              onChange={event =>
                updateMember({
                  managed_teams: event.target.value ? event.target.value.split(',').map(value => value.trim()) : []
                })
              }
              value={member.managed_teams.join(', ')}
            />
          </label>
          <p className="eid-note">{copy.scopeNote}</p>
        </>
      )}
      <p className="eid-note">{copy.providerModelNote}</p>
      <label>
        {t.organizationRuntime.provider}
        <Input
          list="organization-providers"
          maxLength={100}
          onChange={event => updateMember({ provider: event.target.value || null })}
          value={member.provider || ''}
        />
      </label>
      <datalist id="organization-providers">
        {[...new Set(snapshot.agents.map(agent => agent.provider).filter(Boolean))].map(value => (
          <option key={value} value={value!} />
        ))}
      </datalist>
      <label>
        {t.organizationRuntime.model}
        <Input
          list="organization-models"
          maxLength={300}
          onChange={event => updateMember({ model: event.target.value || null })}
          value={member.model || ''}
        />
      </label>
      <datalist id="organization-models">
        {[
          ...new Set(
            snapshot.agents
              .filter(agent => !member.provider || agent.provider === member.provider)
              .map(agent => agent.model)
              .filter(Boolean)
          )
        ].map(value => (
          <option key={value} value={value!} />
        ))}
      </datalist>
      <fieldset>
        <legend>{t.organizationRuntime.tools}</legend>
        {member.role === 'Worker' &&
          management.allowedTools.map(value => (
            <label className="eid-inline" key={value}>
              <input
                checked={member.tool_grants.includes(value)}
                onChange={event => toggle('tool_grants', value, event.target.checked)}
                type="checkbox"
              />
              {value}
            </label>
          ))}
      </fieldset>
      <p className="eid-note">{copy.grantNote}</p>
    </>
  )
}
