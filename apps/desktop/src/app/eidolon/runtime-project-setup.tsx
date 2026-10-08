import { useEffect, useId, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { controlVariants } from '@/components/ui/control'
import { CopyButton } from '@/components/ui/copy-button'
import { ErrorState } from '@/components/ui/error-state'
import { Input } from '@/components/ui/input'
import { Loader } from '@/components/ui/loader'
import { Textarea } from '@/components/ui/textarea'
import { useI18n } from '@/i18n/context'

import { Inspector } from './inspector'
import { organizationErrorMessage } from './runtime-adapter'
import { sameProjectBinding, validProjectBinding } from './runtime-project-setup-contract'
import type { OrganizationProjectBinding, OrganizationProjectDraft, OrganizationProjectSetup, OrganizationSnapshot, RuntimeOrganizationAdapter } from './types'

const emptyProject = (): OrganizationProjectBinding => ({ id: '', root: '', recipe: '', team: '' })

/** Drafts live only in the connection that opened them. Reads never replace edits. */
export function OrganizationProjectSetupForm({ adapter, snapshot, onClose }: {
  adapter: RuntimeOrganizationAdapter
  snapshot: OrganizationSnapshot
  onClose(): void
}) {
  const { t } = useI18n()
  const copy = t.organizationWork
  const roster = t.organizationRoster
  const [setup, setSetup] = useState<OrganizationProjectSetup>()
  const [draft, setDraft] = useState(emptyProject)
  const [pending, setPending] = useState(false)
  const [reviewing, setReviewing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const saveKey = useRef('')
  const saveController = useRef<AbortController | undefined>(undefined)
  const [reading, setReading] = useState(true)
  const [error, setError] = useState('')
  const [generated, setGenerated] = useState<OrganizationProjectDraft>()
  const scope = useRef(snapshot.connection?.scope).current
  const active = useRef(true)
  const order = useRef(0)
  const sending = useRef(false)
  const hintId = useId()
  const current = () => active.current && adapter.getSnapshot().connection?.scope === scope
  const unavailable = snapshot.connection?.state !== 'ready' || snapshot.connection?.scope !== scope
  const disabled = unavailable || reading || pending || reviewing
  const duplicate = setup?.projects.some(project => project.id === draft.id)
  const occupiedRoot = setup?.projects.some(project => project.root === draft.root)
  const atCapacity = !!setup && setup.projects.length >= 8
  const compatible = setup?.recipes.some(recipe => setup.roots.includes(recipe.root)) && !!setup?.teams.length

  const canSave = setup?.storage === 'profile-ledger' && !!adapter.saveProject
  const validated = !!setup && !!generated && generated.revision === setup.revision && sameProjectBinding(generated.project, draft) && validProjectBinding(draft, setup)

  const clearDraft = () => {setGenerated(undefined); setSaved(false); saveKey.current = ''}

  const read = (preserveExport = false) => {
    if (!adapter.getProjectSetup || sending.current) {return}
    const token = ++order.current

    if (!preserveExport) {clearDraft()}
    setReading(true)
    setError('')
    void adapter.getProjectSetup().then(value => {
      if (current() && token === order.current) {setSetup(value)}
    }).catch(reason => {
      if (current() && token === order.current) {setError(organizationErrorMessage(reason))}
    }).finally(() => {
      if (current() && token === order.current) {setReading(false)}
    })
  }

  // eslint-disable-next-line no-restricted-syntax -- read scoped backend state once on explicit open
  useEffect(() => {
    active.current = true
    read()

    return () => {active.current = false; saveController.current?.abort()}
    // eslint-disable-next-line react-hooks/exhaustive-deps -- the parent remounts this form for each scope
  }, [])

  const close = () => {
    if (reviewing) {
      setReviewing(false)

      return
    }

    active.current = false
    order.current++
    saveController.current?.abort()
    onClose()
  }

  const prepare = () => {
    if (!setup || !adapter.prepareProjectDraft || !validProjectBinding(draft, setup) || disabled || sending.current || !current()) {return}
    const token = ++order.current
    sending.current = true
    setPending(true)
    setError('')
    clearDraft()
    void adapter.prepareProjectDraft({ project: { ...draft }, expectedRevision: setup.revision }).then(value => {
      if (current() && token === order.current) {
        saveKey.current = crypto.randomUUID()
        setGenerated(value)
      }
    }).catch(reason => {
      if (current() && token === order.current) {setError(organizationErrorMessage(reason))}
    }).finally(() => {
      sending.current = false

      if (current() && token === order.current) {setPending(false)}
    })
  }

  const save = () => {
    setReviewing(false)

    if (!setup || !generated || !adapter.saveProject || !canSave || !validated || saved || sending.current || !current() || unavailable || reading) {return}
    const token = ++order.current
    const controller = new AbortController()
    saveController.current = controller
    sending.current = true
    setPending(true)
    setSaving(true)
    setError('')
    let completed = false
    void adapter.saveProject({ project: { ...generated.project }, expectedRevision: generated.revision, idempotencyKey: saveKey.current, confirmSave: true }, controller.signal).then(() => {
      if (current() && token === order.current) {
        completed = true
        setSaved(true)
      }
    }).catch(reason => {
      if (current() && token === order.current) {setError(organizationErrorMessage(reason))}
    }).finally(() => {
      sending.current = false

      if (current() && token === order.current) {
        setPending(false)
        setSaving(false)

        if (completed) {read(true)}
      }
    })
  }

  return <Inspector kind="agent" onClose={close} title={copy.projectSetup}>
    <p className="eid-note">{canSave ? copy.projectSaveNote : copy.projectSetupNote}</p>
    {reading && <Loader label={copy.projectSetupLoading} />}
    {error && <div role="alert"><ErrorState description={error} title={copy.projectSetupError} /></div>}
    {setup && <>
      <h3>{copy.projectSetupExisting}</h3>
      {setup.projects.length ? <ul aria-label={copy.projectSetupExisting}>{setup.projects.map(project => <li key={project.id}>{project.id} · {project.root} · {project.recipe} · {project.team}</li>)}</ul> : <p>{copy.projectSetupEmpty}</p>}
      {!!setup.ledgerProjects?.length && <>
        <h3>{copy.projectSaveExisting}</h3>
        <ul aria-label={copy.projectSaveExisting}>{setup.ledgerProjects.map(project => <li key={project.id}>{project.id} · {project.root} · {project.recipe} · {project.team}</li>)}</ul>
      </>}
      {!!setup.registryConflicts?.length && <ul aria-label={copy.projectSaveConflicts}>{setup.registryConflicts.map((reason, index) => <li key={index}>{reason}</li>)}</ul>}
      {setup.repair && <p role="status">{setup.repair}</p>}
      {setup.blocked && <p role="status">{copy.projectSetupBlocked}</p>}
      {atCapacity && <p role="status">{copy.projectSetupLimit}</p>}
      {!compatible && <p role="status">{copy.projectSetupMissing}</p>}
      <form aria-busy={pending} aria-label={copy.projectSetupAdd} className="eid-resolution-form" onSubmit={event => {event.preventDefault(); prepare()}}>
        <fieldset className="eid-management-fields" disabled={disabled || setup.blocked || !compatible || atCapacity}>
          <legend>{copy.projectSetupAdd}</legend>
          <label>{copy.projectSetupId}<Input aria-describedby={hintId} aria-invalid={duplicate || undefined} maxLength={64} onChange={event => {setDraft({ ...draft, id: event.target.value }); clearDraft()}} pattern="[a-z][a-z0-9_\-]{0,63}" required value={draft.id} /></label>
          <p className="eid-note" id={hintId}>{copy.projectSetupIdHint}</p>
          {duplicate && <p role="status">{copy.projectSetupDuplicate}</p>}
          <label>{copy.projectSetupRoot}<select className={controlVariants()} onChange={event => {setDraft({ ...draft, root: event.target.value, recipe: '' }); clearDraft()}} required value={draft.root}>
            <option value="">{copy.projectSetupChoose}</option>{setup.roots.map(root => <option disabled={setup.projects.some(project => project.root === root)} key={root} value={root}>{root}{setup.projects.some(project => project.root === root) ? ` · ${copy.projectSetupOccupied}` : ''}</option>)}
          </select></label>
          {occupiedRoot && <p role="status">{copy.projectSetupRootConflict}</p>}
          <label>{copy.projectSetupRecipe}<select className={controlVariants()} onChange={event => {setDraft({ ...draft, recipe: event.target.value }); clearDraft()}} required value={draft.recipe}>
            <option value="">{copy.projectSetupChoose}</option>{setup.recipes.filter(recipe => recipe.root === draft.root).map(recipe => <option key={recipe.id} value={recipe.id}>{recipe.id}</option>)}
          </select></label>
          <label>{copy.projectSetupTeam}<select className={controlVariants()} onChange={event => {setDraft({ ...draft, team: event.target.value }); clearDraft()}} required value={draft.team}>
            <option value="">{copy.projectSetupChoose}</option>{setup.teams.map(team => <option key={team} value={team}>{team}</option>)}
          </select></label>
        </fieldset>
        <Button disabled={disabled || !validProjectBinding(draft, setup)} type="submit">{saving ? copy.projectSaveSaving : pending ? copy.projectSetupPreparing : copy.projectSetupAdd}</Button>
      </form>
    </>}
    {generated && <>
      <p role="status">{saved ? copy.projectSaveSaved : copy.projectSetupAdded}</p>
      <p className="eid-note">{canSave ? copy.projectSaveDisclosure : copy.projectSetupApplyNote}</p>
      <label>{copy.projectSetupYaml}<Textarea readOnly rows={6} value={generated.yaml} /></label>
      <CopyButton text={generated.yaml} />
      {canSave && !saved && <Button disabled={disabled || !validated} onClick={() => setReviewing(true)} type="button">{copy.projectSaveReview}</Button>}
      {saving && <p role="status">{copy.projectSavePendingNote}</p>}
      {reviewing && <ConfirmDialog
        confirmLabel={copy.projectSaveConfirm}
        description={<>{copy.projectSaveDisclosure}<br />{generated.project.id} · {generated.project.root} · {generated.project.recipe} · {generated.project.team}</>}
        dismissOnConfirm
        onClose={() => setReviewing(false)}
        onConfirm={save}
        open
        title={copy.projectSaveReview}
      />}
    </>}
    <div className="eid-inline">
      <Button disabled={disabled} onClick={() => read()} type="button" variant="secondary">{copy.projectSetupRefresh}</Button>
      <Button onClick={close} type="button" variant="text">{pending ? roster.close : roster.cancel}</Button>
    </div>
  </Inspector>
}
