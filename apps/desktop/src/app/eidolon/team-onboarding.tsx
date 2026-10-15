import './team-onboarding.css'

import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router'

import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/components/ui/dialog'
import { Loader } from '@/components/ui/loader'
import { Textarea } from '@/components/ui/textarea'
import { useI18n } from '@/i18n/context'
import { teamOnboardingCopy } from '@/i18n/organization-team-onboarding'
import { startManualOnboarding } from '@/store/onboarding'
import { $gateway } from '@/store/gateway'

import { subscribeOrganizationDirectorySource } from './organization-directory-runtime'
import { OrganizationOwnerEditor } from './organization-owner-editor'
import {
  createTeamOnboardingClient,
  type TeamDraft,
  type TeamOnboardingClient,
  type TeamPreview,
  type TeamReceipt,
  type TeamSetup
} from './team-onboarding-client'
import { TeamOnboardingEditor } from './team-onboarding-editor'
import type { RuntimeOrganizationAdapter } from './types'
import { readOrganizationMetadataScope, useOrganizationMetadata } from './use-organization-metadata'

function readTeamScope() {
  const scope = readOrganizationMetadataScope()
  const gateway = $gateway.get()
  return {
    ...scope,
    request: <T,>(method: string, params: Record<string, unknown>): Promise<T> => {
      if (!gateway) return Promise.reject(new Error('stale'))
      return gateway.request<T>(method, params, method === 'organization.generateTeam' ? 180000 : 15000)
    }
  }
}

export function TeamOnboardingEntry({ adapter }: { adapter: RuntimeOrganizationAdapter }) {
  const { locale } = useI18n()
  const copy = teamOnboardingCopy[locale]
  const { client: metadataClient, scope, view } = useOrganizationMetadata()
  const [editingIdentity, setEditingIdentity] = useState(false)
  const [opened, setOpened] = useState(false)
  const [completed, setCompleted] = useState(false)
  const [setup, setSetup] = useState<TeamSetup | null>(null)
  const [loadError, setLoadError] = useState(false)
  const [reload, setReload] = useState(0)
  const trigger = useRef<HTMLButtonElement>(null)
  const continueAfterIdentity = useRef(false)
  const initializeAfterIdentity = useRef(false)
  const organizationId = view.descriptor?.metadata?.organizationId ?? null
  const [client, setClient] = useState<TeamOnboardingClient | null>(null)
  useEffect(() => {
    continueAfterIdentity.current = false
    initializeAfterIdentity.current = false
    setCompleted(false)
    setEditingIdentity(false)
  }, [scope.key, scope.ownerKey, scope.profile])
  useEffect(() => {
    setOpened(false)
    setSetup(null)
    setLoadError(false)
    if (!scope.eligible || !organizationId) return
    const next = createTeamOnboardingClient(readTeamScope, subscribeOrganizationDirectorySource)
    setClient(next)
    let active = true
    const load = async () => {
      if (!next.current()) throw new Error('stale')
      // Initialization belongs only to an explicitly confirmed first identity save.
      // Ordinary entry rendering is a read and must never refresh a new owner.
      if (initializeAfterIdentity.current) {
        initializeAfterIdentity.current = false
        await adapter.refresh()
        if (!next.current()) throw new Error('stale')
      }
      return next.setup(organizationId)
    }
    void load()
      .then(result => {
        if (active && next.current()) {
          setSetup(result)
          setLoadError(result.state === 'unconfigured' || result.state === 'unsupported')
          if (continueAfterIdentity.current && result.state === 'available') {
            continueAfterIdentity.current = false
            setOpened(true)
          }
        }
      })
      .catch(() => {
        if (active && next.current()) setLoadError(true)
      })
    return () => {
      active = false
      next.dispose()
      setClient(null)
    }
  }, [scope.key, scope.ownerKey, scope.profile, scope.eligible, organizationId, adapter, reload])
  if (
    (completed && !opened) ||
    !scope.eligible ||
    !view.descriptor ||
    view.descriptor.state === 'unsupported' ||
    (organizationId && setup?.state !== 'available' && !loadError)
  )
    return null
  return (
    <section className="eid-team-entry">
      {loadError && (
        <>
          <p role="alert">{copy.unavailable}</p>
          <Button
            onClick={() => {
              // Retry may resume this owner's explicit initialization intent. Mere
              // rendering never retries it, and a scope change retires the intent.
              if (!client?.current()) return
              initializeAfterIdentity.current = continueAfterIdentity.current
              setReload(value => value + 1)
            }}
            variant="secondary"
          >
            {copy.retry}
          </Button>
        </>
      )}
      <Button
        disabled={loadError}
        onClick={() => (organizationId ? setOpened(true) : setEditingIdentity(true))}
        ref={trigger}
      >
        {organizationId ? copy.generate : copy.setupOrganization}
      </Button>
      {editingIdentity && (
        <OrganizationOwnerEditor
          client={metadataClient}
          draftKey={scope.ownerKey}
          onClose={() => setEditingIdentity(false)}
          onReturnFocus={saved => {
            setEditingIdentity(false)
            const currentScope = readOrganizationMetadataScope()
            if (
              saved &&
              currentScope.eligible &&
              currentScope.key === scope.key &&
              currentScope.ownerKey === scope.ownerKey &&
              currentScope.profile === scope.profile
            ) {
              continueAfterIdentity.current = true
              initializeAfterIdentity.current = true
              setReload(value => value + 1)
            } else trigger.current?.focus()
          }}
        />
      )}
      {opened && setup && client && (
        <TeamOnboardingDialog
          client={client}
          onClose={() => setOpened(false)}
          onCreated={() => {
            setCompleted(true)
            void adapter.refresh()
          }}
          onReturnFocus={() => trigger.current?.focus()}
          profile={scope.profile}
          setup={setup}
        />
      )}
    </section>
  )
}

export function TeamOnboardingDialog({
  client,
  setup: initialSetup,
  profile,
  onClose,
  onCreated,
  onReturnFocus
}: {
  client: TeamOnboardingClient
  setup: TeamSetup
  profile: string
  onClose: () => void
  onCreated: () => void
  onReturnFocus: () => void
}) {
  const { locale } = useI18n()
  const copy = teamOnboardingCopy[locale]
  const [setup, setSetup] = useState(initialSetup)
  const [jobs, setJobs] = useState('')
  const [goals, setGoals] = useState('')
  const [namingStyle, setNamingStyle] = useState('')
  const [team, setTeam] = useState<TeamDraft | null>(null)
  const [preview, setPreview] = useState<TeamPreview | null>(null)
  const [receipt, setReceipt] = useState<TeamReceipt | null>(null)
  const [pending, setPending] = useState<'generate' | 'preview' | 'create' | 'check' | null>(null)
  const [error, setError] = useState('')
  const [unresolvedCreate, setUnresolvedCreate] = useState(false)
  const operation = useRef(0)
  const busy = useRef(false)
  const alive = useRef(true)
  const generationIntent = useRef<string | null>(null)
  const intent = useRef<{ version: string; key: string } | null>(null)
  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
      operation.current++
    }
  }, [])
  const act = async (kind: NonNullable<typeof pending>, action: () => Promise<void>) => {
    if (busy.current || !client.current()) {
      if (!client.current()) setError(copy.stale)
      return
    }
    busy.current = true
    const token = ++operation.current
    setPending(kind)
    setError('')
    try {
      await action()
    } catch (failure) {
      if (alive.current && token === operation.current)
        setError(failure instanceof Error && failure.message === 'stale' ? copy.stale : copy.error)
    } finally {
      if (alive.current && token === operation.current) {
        busy.current = false
        setPending(null)
      }
    }
  }
  const applyPreview = (result: TeamPreview) => {
    if (alive.current) {
      setPreview(result)
      setTeam(result.team)
      intent.current = null
    }
  }
  const update = (value: TeamDraft) => {
    setTeam(value)
    setPreview(null)
    intent.current = null
  }
  const close = () => {
    if (pending === 'create') return
    alive.current = false
    operation.current++
    onClose()
  }
  const modelBlocked = setup.readiness.status === 'blocked'
  return (
    <Dialog
      onOpenChange={open => {
        if (!open) close()
      }}
      open
    >
      <DialogContent
        className="eid-team-dialog"
        onCloseAutoFocus={event => {
          event.preventDefault()
          onReturnFocus()
        }}
        onEscapeKeyDown={event => {
          if (pending === 'create') event.preventDefault()
        }}
      >
        <DialogTitle>{receipt ? copy.ready : copy.title}</DialogTitle>
        <DialogDescription>{receipt ? copy.readyNote : copy.intro}</DialogDescription>
        <div className="eid-team-body">
          {receipt ? (
            <>
              <p className="eid-team-note">{copy.inherited}</p>
              <p role="status">
                {receipt.readiness.status === 'blocked' ? copy.modelBlocked : copy.readinessUnchecked}
              </p>
              <Button asChild>
                <Link onClick={close} to="/home#eid-home-intake">
                  {copy.firstRequest}
                </Link>
              </Button>
            </>
          ) : (
            <>
              <div className="eid-team-actions">
                <Button
                  disabled={!!pending || unresolvedCreate}
                  onClick={() => startManualOnboarding(null, profile)}
                  size="sm"
                  variant="secondary"
                >
                  {copy.connectModel}
                </Button>
                <Button
                  disabled={!!pending || unresolvedCreate}
                  onClick={() =>
                    void act('check', async () => {
                      const result = await client.setup(setup.organizationId ?? null)
                      if (alive.current) setSetup(result)
                    })
                  }
                  size="sm"
                  variant="text"
                >
                  {copy.retry}
                </Button>
              </div>
              {modelBlocked && <p role="status">{copy.modelRequired}</p>}
              {!team ? (
                <>
                  <fieldset className="eid-team-fields" disabled={modelBlocked}>
                    <label>
                      {copy.jobs}
                      <Textarea
                        disabled={!!pending || unresolvedCreate}
                        maxLength={4000}
                        onChange={event => {
                          setJobs(event.target.value)
                          generationIntent.current = null
                        }}
                        placeholder={copy.description}
                        value={jobs}
                      />
                    </label>
                    <label>
                      {copy.goals}
                      <Textarea
                        disabled={!!pending || unresolvedCreate}
                        maxLength={4000}
                        onChange={event => {
                          setGoals(event.target.value)
                          generationIntent.current = null
                        }}
                        value={goals}
                      />
                    </label>
                    <label>
                      {copy.namingStyle}
                      <Textarea
                        disabled={!!pending || unresolvedCreate}
                        maxLength={500}
                        onChange={event => {
                          setNamingStyle(event.target.value)
                          generationIntent.current = null
                        }}
                        value={namingStyle}
                      />
                    </label>
                    <p className="eid-team-note">
                      {copy.generationCost} {copy.generationCancel}
                    </p>
                    <Button
                      disabled={!!pending || modelBlocked || !jobs.trim() || !goals.trim()}
                      onClick={() =>
                        void act('generate', async () =>
                          applyPreview(
                            await client.generate(setup, {
                              jobs,
                              goals,
                              namingStyle,
                              idempotencyKey: (generationIntent.current ??= crypto.randomUUID())
                            })
                          )
                        )
                      }
                    >
                      {copy.generate}
                    </Button>
                  </fieldset>
                  <details>
                    <summary>{copy.manual}</summary>
                    <Button
                      disabled={!!pending || unresolvedCreate}
                      onClick={() =>
                        update({ executive: { name: '', purpose: '', responsibilities: [] }, members: [] })
                      }
                      variant="secondary"
                    >
                      {copy.manualStart}
                    </Button>
                    <p>{copy.presets}</p>
                    <div className="eid-team-actions">
                      {setup.presets.map(preset => (
                        <Button
                          disabled={!!pending || unresolvedCreate}
                          key={preset.id}
                          onClick={() => update(structuredClone(preset.team))}
                          size="sm"
                          variant="secondary"
                        >
                          {preset.id === 'research-writing' ? copy.researchPreset : preset.name}
                        </Button>
                      ))}
                    </div>
                  </details>
                </>
              ) : (
                <TeamOnboardingEditor
                  copy={copy}
                  disabled={!!pending || unresolvedCreate}
                  onChange={update}
                  team={team}
                />
              )}
              <p className="eid-team-note">{copy.authority}</p>
              {preview && (
                <>
                  <p className="eid-team-note">{copy.inherited}</p>
                  <p>{copy.retainedSupport}</p>
                  {!!preview.supportAgents?.length && (
                    <div>
                      <p>{copy.supportIdentities}</p>
                      <ul>
                        {preview.supportAgents.map(member => (
                          <li key={member.id}>
                            <bdi>{member.name}</bdi>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </>
              )}
            </>
          )}
          {(receipt?.readiness ?? preview?.readiness)?.warnings?.includes('new_member_routes_unassigned') && (
            <p className="eid-team-note">{copy.inheritedRoutes}</p>
          )}
          {pending && (
            <Loader
              label={pending === 'generate' ? copy.generating : pending === 'create' ? copy.creating : copy.checking}
            />
          )}
          {error && <p role="alert">{error}</p>}
        </div>
        <footer className="eid-team-actions">
          <Button disabled={pending === 'create'} onClick={close} variant="text">
            {copy.cancel}
          </Button>
          {team && !receipt && (
            <>
              <Button
                disabled={!!pending || unresolvedCreate}
                onClick={() => {
                  setTeam(null)
                  setPreview(null)
                }}
                variant="secondary"
              >
                {copy.back}
              </Button>
              {!preview ? (
                <Button
                  disabled={!!pending || unresolvedCreate}
                  onClick={() => void act('preview', async () => applyPreview(await client.preview(setup, team)))}
                >
                  {copy.validate}
                </Button>
              ) : (
                <Button
                  disabled={!!pending}
                  onClick={() =>
                    void act('create', async () => {
                      setUnresolvedCreate(true)
                      intent.current ??= { version: preview.previewVersion, key: crypto.randomUUID() }
                      const result = await client.create(preview, intent.current.key)
                      if (alive.current) {
                        setReceipt(result)
                        setUnresolvedCreate(false)
                        onCreated()
                      }
                    })
                  }
                >
                  {copy.create}
                </Button>
              )}
            </>
          )}
        </footer>
      </DialogContent>
    </Dialog>
  )
}
