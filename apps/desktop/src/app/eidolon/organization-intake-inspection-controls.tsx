import { useId, useSyncExternalStore } from 'react'

import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { controlVariants } from '@/components/ui/control'
import { Loader } from '@/components/ui/loader'
import { Textarea } from '@/components/ui/textarea'
import { useI18n } from '@/i18n/context'
import { intakeInspectionCopy } from '@/i18n/organization-intake-inspection'

import { createIntakeInspectionClient, inspectionContextFiles } from './organization-intake-inspection-client'
import { readOrganizationMetadataScope, useOrganizationMetadata } from './use-organization-metadata'

const client = createIntakeInspectionClient(readOrganizationMetadataScope)

export function OrganizationIntakeInspectionControls() {
  const { locale } = useI18n()
  const copy = intakeInspectionCopy[locale]
  const { scope } = useOrganizationMetadata()
  const view = useSyncExternalStore(client.subscribe, client.getSnapshot)
  const { descriptor, draft, preview, intent, busy } = view
  const id = useId()
  const disabled = !scope.eligible || busy || !!intent || !!preview
  const worker = descriptor?.workers.find(item => item.id === draft?.workerId)
  const projects = descriptor?.projects.filter(item => item.team === worker?.team) ?? []
  const reviewed = preview ?? intent
  const named = reviewed?.enabled ? reviewed : descriptor
  const workerName = descriptor?.workers.find(item => item.id === named?.workerId)?.name ?? named?.workerId

  const projectNames = named?.projectIds
    .map(projectId => {
      const project = descriptor?.projects.find(item => item.id === projectId)

      return project ? `${project.id} · ${project.root}` : projectId
    })
    .join(', ')

  return (
    <section aria-label={copy.title} className="grid gap-2">
      <details className="eid-form-disclosure">
        <summary>{copy.title}</summary>
        <div className="grid gap-3">
          <p className="eid-note">{copy.note}</p>
          <Button disabled={disabled} onClick={() => void client.read()} type="button" variant="secondary">
            {copy.read}
          </Button>
          {descriptor && draft && (
            <>
              <p>
                {descriptor.enabled ? copy.active : copy.inactive} {copy.revision}: {descriptor.revision}
              </p>
              {!descriptor.organizationId && <p>{copy.unavailable}</p>}
              <label className="flex items-start gap-2" htmlFor={`${id}-enabled`}>
                <Checkbox
                  checked={draft.enabled}
                  disabled={disabled || !descriptor.organizationId}
                  id={`${id}-enabled`}
                  onCheckedChange={value => client.edit({ enabled: value === true })}
                />
                {copy.enabled}
              </label>
              {draft.enabled && (
                <>
                  <label htmlFor={`${id}-worker`}>{copy.worker}</label>
                  <select
                    className={controlVariants()}
                    disabled={disabled}
                    id={`${id}-worker`}
                    onChange={event => client.edit({ workerId: event.target.value })}
                    value={draft.workerId}
                  >
                    <option value="">{copy.chooseWorker}</option>
                    {descriptor.workers.map(item => (
                      <option key={item.id} value={item.id}>
                        {item.name} · {item.id} · {item.team}
                      </option>
                    ))}
                  </select>
                  <fieldset className="grid gap-2" disabled={disabled}>
                    <legend>{copy.projects}</legend>
                    {!projects.length && <p className="eid-note">{copy.noProjects}</p>}
                    {projects.map(project => (
                      <label
                        className="flex items-start gap-2"
                        htmlFor={`${id}-project-${project.id}`}
                        key={project.id}
                      >
                        <Checkbox
                          checked={draft.projectIds.includes(project.id)}
                          disabled={disabled}
                          id={`${id}-project-${project.id}`}
                          onCheckedChange={value =>
                            client.edit({
                              projectIds:
                                value === true
                                  ? [...draft.projectIds, project.id]
                                  : draft.projectIds.filter(item => item !== project.id)
                            })
                          }
                        />
                        <bdi>
                          {project.id} · {project.root}
                        </bdi>
                      </label>
                    ))}
                  </fieldset>
                  <label htmlFor={`${id}-paths`}>{copy.paths}</label>
                  <Textarea
                    aria-describedby={`${id}-path-help`}
                    dir="ltr"
                    disabled={disabled}
                    id={`${id}-paths`}
                    maxLength={1024}
                    onChange={event => client.edit({ pathsText: event.target.value })}
                    placeholder={'root0/README.md\nroot0/context.md'}
                    rows={4}
                    value={draft.pathsText}
                  />
                  <p className="eid-note" id={`${id}-path-help`}>
                    {copy.pathHelp} <bdi>{inspectionContextFiles.join(', ')}</bdi>
                  </p>
                </>
              )}
              <p className="eid-note">{copy.authority}</p>
              <p className="eid-note">{copy.bounds}</p>
              <p className="eid-note">{copy.safety}</p>
              <div>
                <Button disabled={disabled || !descriptor.organizationId} onClick={() => client.review()} type="button">
                  {copy.review}
                </Button>
              </div>
            </>
          )}
        </div>
      </details>
      {!scope.eligible && <p>{copy.unavailable}</p>}
      {busy && <Loader />}
      {view.error && <p role="alert">{copy[view.error]}</p>}
      {view.saved && <p role="status">{copy.saved}</p>}
      {intent && (
        <div className="grid gap-2">
          <p>{intent.enabled ? copy.enableReview : copy.disableReview}</p>
          {named?.workerId && (
            <p dir="auto">
              {copy.worker}: {workerName} · {named.workerId}
            </p>
          )}
          {!!named?.projectIds.length && (
            <p dir="auto">
              {copy.projects}: {projectNames}
            </p>
          )}
          {!!named?.paths.length && <p dir="ltr">{named.paths.join(', ')}</p>}
          <div>
            <Button
              disabled={!scope.eligible || busy}
              onClick={() => void client.retry()}
              type="button"
              variant="secondary"
            >
              {copy.retry}
            </Button>
          </div>
        </div>
      )}
      {preview && (
        <ConfirmDialog
          cancelLabel={copy.cancel}
          confirmLabel={copy.confirm}
          description={
            <>
              {preview.enabled ? copy.authority : copy.disableNote}
              <br />
              {named?.workerId && (
                <>
                  {copy.worker}:{' '}
                  <bdi>
                    {workerName} · {named.workerId}
                  </bdi>
                  <br />
                </>
              )}
              {!!named?.projectIds.length && (
                <>
                  {copy.projects}: <bdi>{projectNames}</bdi>
                  <br />
                </>
              )}
              {!!named?.paths.length && (
                <>
                  <bdi>{named.paths.join(', ')}</bdi>
                  <br />
                </>
              )}
              {copy.revision}: {preview.expectedRevision}
              <br />
              {preview.enabled && (
                <>
                  {copy.bounds}
                  <br />
                  {copy.safety}
                </>
              )}
            </>
          }
          dismissOnConfirm
          onClose={client.cancel}
          onConfirm={() => void client.confirm()}
          open
          title={preview.enabled ? copy.enableReview : copy.disableReview}
        />
      )}
    </section>
  )
}
