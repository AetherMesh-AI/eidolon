import { useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Loader } from '@/components/ui/loader'
import { LogView } from '@/components/ui/log-view'
import { useI18n } from '@/i18n/context'
import { modelRoutingCopy } from '@/i18n/organization-model-routing'

import { LocalizedTime } from './localized-time'
import { RuntimeModelDispatch } from './runtime-model-dispatch'
import type { OrganizationExecutionAudit, OrganizationRequest, RuntimeOrganizationAdapter } from './types'

interface RuntimeExecutionAuditProps {
  adapter: RuntimeOrganizationAdapter
  request: OrganizationRequest
}

export function RuntimeExecutionAudit({ adapter, request }: RuntimeExecutionAuditProps) {
  const { t, locale } = useI18n()
  const copy = t.organizationWork
  const routingCopy = modelRoutingCopy[locale]
  const [requested, setRequested] = useState(false)
  const [audit, setAudit] = useState<OrganizationExecutionAudit | null>(null)
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    let current = true
    setAudit(null)
    setError('')

    if (requested && adapter.getExecutionAudit) {
      void adapter
        .getExecutionAudit(request.id)
        .then(value => {
          if (current) {
            setAudit(value)
          }
        })
        .catch(reason => {
          if (current) {
            setError(reason instanceof Error ? reason.message : copy.executionAuditInvalid)
          }
        })
    }

    return () => {
      current = false
    }
  }, [adapter, requested, request.id, request.attempts, request.status, attempt, copy.executionAuditInvalid])

  if (!requested) {
    return (
      <Button onClick={() => setRequested(true)} size="sm" variant="secondary">
        {copy.inspectExecutionAudit}
      </Button>
    )
  }

  return (
    <section aria-label={copy.executionAudit}>
      <h3>{copy.executionAudit}</h3>
      <p className="eid-note">{copy.executionAuditNote}</p>
      {!audit && !error && <Loader label={copy.executionAuditLoading} />}
      {error && <p role="alert">{error}</p>}
      {(audit || error) && (
        <Button onClick={() => setAttempt(value => value + 1)} size="sm" variant="secondary">
          {copy.refresh}
        </Button>
      )}
      {audit && (
        <>
          {!audit.contexts.length && !audit.evidencePasses.length && !audit.modelCalls.length && !audit.modelDispatches?.length && (
            <p>{copy.executionAuditEmpty}</p>
          )}
          {audit.contexts.length > 0 && (
            <section aria-label={copy.contextRecords}>
              <h4>{copy.contextRecords}</h4>
              <ol className="eid-list">
                {audit.contexts.map((row, index) => (
                  <li key={`${row.attemptToken}:${index}`}>
                    <p>
                      {copy.contextMode}: {typeof row.report.mode === 'string' ? row.report.mode : copy.directContext}
                    </p>
                    <p>
                      {copy.status}: {typeof row.report.status === 'string' ? row.report.status : copy.unknownUsage}
                    </p>
                    <LocalizedTime value={row.createdAt} />
                    <LogView className="max-h-96">{JSON.stringify(row.report, null, 2)}</LogView>
                  </li>
                ))}
              </ol>
            </section>
          )}
          {audit.evidencePasses.length > 0 && (
            <section aria-label={copy.evidenceReadPasses}>
              <h4>{copy.evidenceReadPasses}</h4>
              <ol className="eid-list">
                {audit.evidencePasses.map((row, index) => (
                  <li key={`${row.attemptToken}:${index}`}>
                    <LocalizedTime value={row.createdAt} />
                    <LogView className="max-h-96">{JSON.stringify(row.report, null, 2)}</LogView>
                  </li>
                ))}
              </ol>
            </section>
          )}
          {Boolean(audit.modelDispatches?.length) && (
            <section aria-label={routingCopy.dispatches}>
              <h4>{routingCopy.dispatches}</h4>
              {audit.modelDispatches!.map(dispatch => <RuntimeModelDispatch dispatch={dispatch} key={dispatch.id} />)}
            </section>
          )}
          {audit.modelCalls.length > 0 && (
            <section aria-label={copy.modelCallReservations}>
              <h4>{copy.modelCallReservations}</h4>
              <ol className="eid-list">
                {audit.modelCalls.map(call => (
                  <li key={call.id}>
                    <p>
                      {call.provider} / {call.model}
                    </p>
                    <p>{call.id}</p>
                    {(audit.modelDispatches !== undefined || call.dispatchId !== undefined) && <p>{call.dispatchId ? <>{routingCopy.dispatchId}: <bdi>{call.dispatchId}</bdi></> : routingCopy.legacyCall}</p>}
                    <LocalizedTime value={call.createdAt} />
                    <p>
                      {copy.reservedInputOutput}: {call.input_limit} / {call.output_limit}
                    </p>
                    <p>
                      {copy.reservedCallCost}: {call.reserved_cost_usd ?? copy.unknownUsage}
                    </p>
                  </li>
                ))}
              </ol>
            </section>
          )}
        </>
      )}
    </section>
  )
}
