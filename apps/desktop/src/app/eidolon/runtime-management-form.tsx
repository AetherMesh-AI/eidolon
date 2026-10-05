import { useEffect, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { controlVariants } from '@/components/ui/control'
import { Input } from '@/components/ui/input'
import { useI18n } from '@/i18n/context'

import { Inspector } from './inspector'
import { newMember, validOrganizationConfiguration } from './runtime-management'
import { OrganizationTransferFields } from './runtime-management-transfers'
import { OrganizationMemberFields } from './runtime-member-fields'
import type {
  OrganizationManagement,
  OrganizationMemberConfiguration,
  OrganizationSnapshot,
  RuntimeOrganizationAdapter
} from './types'

export function OrganizationManagementForm({
  adapter,
  snapshot,
  management,
  onClose
}: {
  adapter: RuntimeOrganizationAdapter
  snapshot: OrganizationSnapshot
  management: OrganizationManagement
  onClose(): void
}) {
  const { t } = useI18n()
  const copy = t.organizationRoster
  const [baseline, setBaseline] = useState(management)
  const [draft, setDraft] = useState(() => structuredClone(management.configuration))
  const [selected, setSelected] = useState(0)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const active = useRef(true)
  const sending = useRef(false)
  const member = draft.roster[selected]
  const stale = baseline.generation !== management.generation
  const unavailable = snapshot.connection?.state !== 'ready'
  const disabled = pending || unavailable || stale
  const valid = validOrganizationConfiguration(draft, baseline, snapshot)
  // eslint-disable-next-line no-restricted-syntax -- fence dismissed asynchronous form results
  useEffect(() => {
    active.current = true

    return () => {
      active.current = false
    }
  }, [])

  const updateMember = (values: Partial<OrganizationMemberConfiguration>) => {
    setDraft(current => ({
      ...current,
      roster: current.roster.map((item, index) => (index === selected ? { ...item, ...values } : item))
    }))
    setError('')
  }

  const save = () => {
    if (!valid || disabled || sending.current) {
      return
    }

    sending.current = true
    setPending(true)
    setError('')
    void adapter
      .configureOrganization({ configuration: draft, expectedGeneration: baseline.generation })
      .then(() => {
        if (active.current) {
          onClose()
        }
      })
      .catch(reason => {
        if (active.current) {
          setError(reason instanceof Error ? reason.message : copy.invalid)
        }
      })
      .finally(() => {
        sending.current = false

        if (active.current) {
          setPending(false)
        }
      })
  }

  return (
    <Inspector kind="agent" onClose={onClose} title={copy.manage}>
      <p className="eid-note">{copy.configurationNote}</p>
      <p>
        {copy.generation}: {baseline.generation}
      </p>
      {stale && (
        <div role="alert">
          <p>{copy.stale}</p>
          <Button
            onClick={() => {
              setBaseline(management)
              setDraft(structuredClone(management.configuration))
              setSelected(0)
              setError('')
            }}
            type="button"
            variant="secondary"
          >
            {copy.reload}
          </Button>
        </div>
      )}
      <form
        aria-busy={pending}
        aria-label={copy.manage}
        className="eid-resolution-form"
        onSubmit={event => {
          event.preventDefault()
          save()
        }}
      >
        <fieldset className="eid-management-fields" disabled={disabled}>
          <legend>{copy.roster}</legend>
          {draft.roster.length > 0 && (
            <label>
              {copy.member}
              <select
                className={controlVariants()}
                onChange={event => setSelected(Number(event.target.value))}
                value={selected}
              >
                {draft.roster.map((item, index) => (
                  <option key={index} value={index}>
                    {item.name || copy.newMember} ({item.id || '∅'})
                  </option>
                ))}
              </select>
            </label>
          )}
          <Button
            disabled={draft.roster.length >= draft.max_members}
            onClick={() => {
              setSelected(draft.roster.length)
              setDraft({ ...draft, roster: [...draft.roster, newMember()] })
            }}
            size="sm"
            type="button"
            variant="secondary"
          >
            {copy.addMember}
          </Button>
          {member && (
            <OrganizationMemberFields
              existing={selected < baseline.configuration.roster.length}
              management={baseline}
              member={member}
              roster={draft.roster}
              snapshot={snapshot}
              updateMember={updateMember}
            />
          )}
        </fieldset>
        <fieldset className="eid-management-fields" disabled={disabled}>
          <legend>{copy.maxInflight}</legend>
          <p className="eid-note">{copy.capacityNote}</p>
          <label>
            {copy.maxInflight}
            <Input
              max={4}
              min={1}
              onChange={event => setDraft({ ...draft, max_inflight: Number(event.target.value) })}
              required
              type="number"
              value={draft.max_inflight}
            />
          </label>
          <label>
            {copy.maxMembers}
            <Input
              max={64}
              min={Math.max(1, draft.roster.length)}
              onChange={event => setDraft({ ...draft, max_members: Number(event.target.value) })}
              required
              type="number"
              value={draft.max_members}
            />
          </label>
        </fieldset>
        <OrganizationTransferFields
          configuration={draft}
          disabled={disabled}
          onChange={transfers => setDraft({ ...draft, transfers })}
          snapshot={snapshot}
        />
        {!valid && <p role="status">{copy.invalid}</p>}
        {error && <p role="alert">{error}</p>}
        <div className="eid-inline">
          <Button disabled={disabled || !valid} type="submit">
            {pending ? copy.saving : copy.save}
          </Button>
          <Button onClick={onClose} type="button" variant="text">
            {pending ? copy.close : copy.cancel}
          </Button>
        </div>
      </form>
    </Inspector>
  )
}
