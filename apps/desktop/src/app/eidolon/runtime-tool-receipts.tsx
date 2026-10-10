import { useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Loader } from '@/components/ui/loader'
import { LogView } from '@/components/ui/log-view'
import { useI18n } from '@/i18n/context'

import { LocalizedTime } from './localized-time'
import type {
  OrganizationRequest,
  OrganizationToolEvidence,
  OrganizationToolReceipt,
  RuntimeOrganizationAdapter
} from './types'

const receiptStatuses = ['completed', 'running', 'blocked', 'failed', 'unknown'] as const
const previewLimit = 2000
const receiptLimit = 20

function receiptStatus(receipt: OrganizationToolReceipt) {
  return receiptStatuses.find(status => status === receipt.status) ?? 'unknown'
}

export function ToolReceiptSummary({
  receipts,
  full = false
}: {
  receipts?: OrganizationToolReceipt[]
  full?: boolean
}) {
  const { t } = useI18n()
  const copy = t.organizationRuntime
  const visible = full ? (receipts ?? []) : (receipts?.slice(-receiptLimit) ?? [])

  if (!visible.length) {
    return null
  }

  return (
    <small>
      {full ? copy.receipts : copy.recentReceipts}:{' '}
      {receiptStatuses
        .map(
          status =>
            `${copy.statusLabels[status]}: ${visible.filter(receipt => receiptStatus(receipt) === status).length}`
        )
        .join(' · ')}
    </small>
  )
}

interface RuntimeToolReceiptsProps {
  receipts?: OrganizationToolReceipt[] | OrganizationToolEvidence[]
  full?: boolean
  artifact?: boolean
}

/** Exact artifact and request-audit reads supply full results. Request snapshots
 * carry bounded previews, never an invented successful result for a denial. */
export function RuntimeToolReceipts({ receipts, full = false, artifact = false }: RuntimeToolReceiptsProps) {
  const { t } = useI18n()
  const copy = t.organizationRuntime
  const visible = full ? (receipts ?? []) : (receipts?.slice(-receiptLimit) ?? [])

  return (
    <section aria-label={copy.receipts}>
      <h3>{copy.receipts}</h3>
      {!visible.length ? (
        <p>{copy.noReceipts}</p>
      ) : (
        <>
          <p className="eid-note">{artifact ? copy.artifactNote : full ? copy.requestAuditNote : copy.previewNote}</p>
          <p className="eid-note">{copy.receiptNote}</p>
          <ToolReceiptSummary full={full} receipts={visible} />
          <ol className="eid-list eid-tool-receipts">
            {visible.map(receipt => {
              const status = receiptStatus(receipt)

              const result =
                full && 'result' in receipt && typeof receipt.result === 'string'
                  ? receipt.result
                  : receipt.resultPreview?.slice(0, previewLimit)

              const hasResult = result !== undefined && result !== ''

              return (
                <li key={receipt.id}>
                  <h4>
                    {receipt.toolName}{' '}
                    <span className={`eid-status eid-tool-${status}`}>{copy.statusLabels[status]}</span>
                  </h4>
                  <dl className="eid-runtime-facts">
                    <dt>{copy.receiptId}</dt>
                    <dd>{receipt.id}</dd>
                    {receipt.attempt !== undefined && (
                      <>
                        <dt>{copy.attempt}</dt>
                        <dd>{receipt.attempt}</dd>
                      </>
                    )}
                    {receipt.requestId && (
                      <>
                        <dt>{copy.requestId}</dt>
                        <dd>{receipt.requestId}</dd>
                      </>
                    )}
                    <dt>{copy.toolCallId}</dt>
                    <dd>{receipt.toolCallId}</dd>
                    {receipt.arguments.path !== undefined && (
                      <>
                        <dt>{copy.path}</dt>
                        <dd>{receipt.arguments.path}</dd>
                      </>
                    )}
                    {receipt.arguments.offset !== undefined && (
                      <>
                        <dt>{copy.offset}</dt>
                        <dd>{receipt.arguments.offset}</dd>
                      </>
                    )}
                    {receipt.arguments.limit !== undefined && (
                      <>
                        <dt>{copy.limit}</dt>
                        <dd>{receipt.arguments.limit}</dd>
                      </>
                    )}
                    <dt>{copy.created}</dt>
                    <dd>
                      <LocalizedTime value={receipt.createdAt} />
                    </dd>
                    {receipt.completedAt && (
                      <>
                        <dt>{copy.completed}</dt>
                        <dd>
                          <LocalizedTime value={receipt.completedAt} />
                        </dd>
                      </>
                    )}
                    {receipt.reason && (
                      <>
                        <dt>{copy.reason}</dt>
                        <dd>{receipt.reason}</dd>
                      </>
                    )}
                    {receipt.resultSha256 && (
                      <>
                        <dt>{copy.digest}</dt>
                        <dd>{receipt.resultSha256}</dd>
                      </>
                    )}
                  </dl>
                  {hasResult && (
                    <>
                      <h4>{full ? copy.result : copy.preview}</h4>
                      <LogView className="max-h-96">{result}</LogView>
                    </>
                  )}
                  {status !== 'completed' && <p className="eid-note">{copy.noResult}</p>}
                </li>
              )
            })}
          </ol>
        </>
      )}
    </section>
  )
}

export function RuntimeRequestToolReceipts({
  adapter,
  request
}: {
  adapter: RuntimeOrganizationAdapter
  request: OrganizationRequest
}) {
  const { t } = useI18n()
  const copy = t.organizationRuntime
  const [receipts, setReceipts] = useState<OrganizationToolEvidence[] | null>(null)
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const hasReceipts = (request.toolReceiptCount ?? request.toolReceipts?.length ?? 0) > 0

  const receiptVersion = JSON.stringify([
    request.toolReceiptCount,
    request.toolReceiptsTruncated,
    request.status,
    request.toolReceipts
  ])

  useEffect(() => {
    let current = true
    setReceipts(null)
    setError('')

    if (hasReceipts) {
      void adapter
        .getToolReceipts(request.id)
        .then(value => {
          if (current) {
            setReceipts(value)
          }
        })
        .catch(reason => {
          if (current) {
            setError(reason instanceof Error ? reason.message : copy.auditError)
          }
        })
    }

    return () => {
      current = false
    }
  }, [adapter, request.id, hasReceipts, receiptVersion, attempt, copy.auditError])

  if (!hasReceipts) {
    return <RuntimeToolReceipts receipts={[]} />
  }

  return (
    <>
      {receipts === null && !error && <Loader label={copy.auditLoading} />}
      {error && (
        <>
          <p role="alert">{error}</p>
          <Button onClick={() => setAttempt(value => value + 1)} size="sm" variant="secondary">
            {copy.auditRetry}
          </Button>
        </>
      )}
      {receipts !== null && <RuntimeToolReceipts full receipts={receipts} />}
    </>
  )
}
