import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import type { TeamOnboardingCopy } from '@/i18n/organization-team-onboarding'

import type { TeamDraft, TeamMember } from './team-onboarding-client'

export function TeamOnboardingEditor({
  team,
  onChange,
  copy,
  disabled
}: {
  team: TeamDraft
  onChange: (team: TeamDraft) => void
  copy: TeamOnboardingCopy
  disabled: boolean
}) {
  const update = (index: number, patch: Partial<TeamMember>) =>
    onChange({
      ...team,
      members: team.members.map((member, i) =>
        i === index
          ? { ...member, ...patch }
          : patch.team !== undefined &&
              team.members[index].role === 'Manager' &&
              member.manager_id === team.members[index].id
            ? { ...member, team: patch.team }
            : member
      )
    })
  const add = (role: TeamMember['role']) => {
    const id = `${role.toLowerCase()}-${crypto.randomUUID().slice(0, 8)}`
    onChange({
      ...team,
      members: [
        ...team.members,
        {
          id,
          name: '',
          role,
          manager_id:
            role === 'Manager' ? 'executive' : (team.members.find(member => member.role === 'Manager')?.id ?? ''),
          team: role === 'Manager' ? id : (team.members.find(member => member.role === 'Manager')?.team ?? ''),
          capabilities: role === 'Manager' ? ['request.plan', 'request.integrate'] : ['work.draft', 'work.analyze'],
          purpose: '',
          responsibilities: []
        }
      ]
    })
  }
  return (
    <fieldset className="eid-team-fields" disabled={disabled}>
      <legend>{copy.review}</legend>
      <p className="eid-team-note">{copy.reviewHint}</p>
      <div className="eid-team-executive">
        <strong>{copy.executive}</strong>
        <label>
          {copy.name}
          <Input
            maxLength={100}
            onChange={event => onChange({ ...team, executive: { ...team.executive, name: event.target.value } })}
            value={team.executive.name}
          />
        </label>
        <label>
          {copy.purpose}
          <Textarea
            maxLength={2000}
            onChange={event => onChange({ ...team, executive: { ...team.executive, purpose: event.target.value } })}
            value={team.executive.purpose}
          />
        </label>
        <label>
          {copy.responsibilities}
          <Textarea
            onChange={event =>
              onChange({ ...team, executive: { ...team.executive, responsibilities: event.target.value.split('\n') } })
            }
            value={team.executive.responsibilities.join('\n')}
          />
        </label>
      </div>
      {team.members.map((member, index) => (
        <details className="eid-team-member" key={member.id} open={undefined}>
          <summary>
            <bdi>{member.name || member.id}</bdi> · {member.role === 'Manager' ? copy.manager : copy.worker} ·{' '}
            <bdi>
              {member.manager_id === 'executive'
                ? team.executive.name
                : team.members.find(item => item.id === member.manager_id)?.name}
            </bdi>
          </summary>
          <label>
            {copy.name}
            <Input
              maxLength={100}
              onChange={event => update(index, { name: event.target.value })}
              value={member.name}
            />
          </label>
          <p>
            {copy.role}: {member.role === 'Manager' ? copy.manager : copy.worker}
          </p>
          <label>
            {copy.reportsTo}
            <select
              onChange={event =>
                update(index, {
                  manager_id: event.target.value,
                  team: team.members.find(item => item.id === event.target.value)?.team ?? member.team
                })
              }
              value={member.manager_id}
            >
              {member.role === 'Manager' ? (
                <option value="executive">{team.executive.name}</option>
              ) : (
                <>
                  <option value="">—</option>
                  {team.members
                    .filter(item => item.role === 'Manager')
                    .map(item => (
                      <option key={item.id} value={item.id}>
                        {item.name || item.id}
                      </option>
                    ))}
                </>
              )}
            </select>
          </label>
          <label>
            {copy.scope}
            <Input
              disabled={member.role === 'Worker'}
              onChange={event => update(index, { team: event.target.value })}
              value={member.team}
            />
          </label>
          <label>
            {copy.purpose}
            <Textarea onChange={event => update(index, { purpose: event.target.value })} value={member.purpose} />
          </label>
          <label>
            {copy.responsibilities}
            <Textarea
              onChange={event => update(index, { responsibilities: event.target.value.split('\n') })}
              value={member.responsibilities.join('\n')}
            />
          </label>
          <fieldset className="eid-team-fields">
            <legend>{copy.capabilities}</legend>
            {(member.role === 'Manager'
              ? [
                  ['request.plan', copy.capabilityPlan],
                  ['request.integrate', copy.capabilityIntegrate]
                ]
              : [
                  ['work.draft', copy.capabilityDraft],
                  ['work.analyze', copy.capabilityAnalyze]
                ]
            ).map(([capability, label]) => (
              <label className="eid-team-capability" key={capability}>
                <input
                  type="checkbox"
                  checked={member.capabilities.includes(capability)}
                  disabled={member.role === 'Manager'}
                  onChange={event =>
                    update(index, {
                      capabilities: event.target.checked
                        ? [...member.capabilities, capability]
                        : member.capabilities.filter(value => value !== capability)
                    })
                  }
                />
                {label}
              </label>
            ))}
          </fieldset>
          <Button
            onClick={() => onChange({ ...team, members: team.members.filter((_, i) => i !== index) })}
            size="sm"
            variant="text"
          >
            {copy.remove}
          </Button>
        </details>
      ))}
      <div className="eid-team-actions">
        <Button disabled={team.members.length >= 12} onClick={() => add('Manager')} size="sm" variant="secondary">
          {copy.addManager}
        </Button>
        <Button
          disabled={!team.members.some(member => member.role === 'Manager') || team.members.length >= 12}
          onClick={() => add('Worker')}
          size="sm"
          variant="secondary"
        >
          {copy.addWorker}
        </Button>
      </div>
    </fieldset>
  )
}
