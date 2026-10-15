import { useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { useI18n } from '@/i18n/context'
import { outcomeContractCopy } from '@/i18n/organization-outcome-contracts'

import { type OutcomeJudgmentInput, validOutcomeContract } from './runtime-outcome-contract-types'
import type { Objective, RuntimeOrganizationAdapter } from './types'
import { useOrganizationMetadata } from './use-organization-metadata'

export function RuntimeOutcomeJudgment(props: { objective: Objective; adapter: RuntimeOrganizationAdapter }) {
  const contract = props.objective.acceptance?.contract

  if (!validOutcomeContract(contract) || !contract.criteria.some(c => c.kind === 'human_judgment' && !c.legacy)) {return null}

  return <ConnectedOutcomeJudgment {...props} />
}

function ConnectedOutcomeJudgment({ objective, adapter }: { objective: Objective; adapter: RuntimeOrganizationAdapter }) {
  const metadata = useOrganizationMetadata()
  const expectedOrganizationId = metadata.view.descriptor?.metadata?.organizationId
  const eligible = metadata.scope.eligible && metadata.scope.ownerKey === adapter.getSnapshot().connection?.ownerScope

  return <OutcomeJudgmentPanel adapter={adapter}
    expectedOrganizationId={eligible ? expectedOrganizationId : undefined} key={JSON.stringify([metadata.scope.ownerKey, expectedOrganizationId, objective.id, objective.acceptance?.contract?.sha256, objective.acceptance?.deliverableSha256])} objective={objective} />
}

export function OutcomeJudgmentPanel({ objective, adapter, expectedOrganizationId }: {
  objective: Objective; adapter: RuntimeOrganizationAdapter; expectedOrganizationId?: string
}) {
  const { locale } = useI18n()
  const copy = outcomeContractCopy[locale]
  const contract = objective.acceptance?.contract

  if (!validOutcomeContract(contract) || contract.objectiveId !== objective.id) {return null}

  return <>{contract.criteria.filter(c => c.kind === 'human_judgment' && !c.legacy).map(criterion =>
    <CriterionJudgment adapter={adapter} criterionId={criterion.id} expectedOrganizationId={expectedOrganizationId} key={criterion.id} objective={objective} />)}<p className="eid-note">{copy.note}</p></>
}

function CriterionJudgment({ objective, adapter, criterionId, expectedOrganizationId }: {
  objective: Objective; adapter: RuntimeOrganizationAdapter; criterionId: string; expectedOrganizationId?: string
}) {
  const { locale } = useI18n()
  const copy = outcomeContractCopy[locale]
  const [reason, setReason] = useState('')
  const [confirmed, setConfirmed] = useState(false)
  const [intent, setIntent] = useState<OutcomeJudgmentInput | null>(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const lock = useRef(false)
  const initialScope = useRef(adapter.getSnapshot().connection?.scope)
  const initialOwnerScope = useRef(adapter.getSnapshot().connection?.ownerScope)
  const contract = objective.acceptance!.contract!
  const criterion = contract.criteria.find(c => c.id === criterionId)!
  const digest = objective.acceptance?.deliverableSha256
  const latest = objective.acceptance?.humanJudgments?.filter(j => j.criterionId === criterionId && j.contractSha256 === contract.sha256 && j.deliverableSha256 === digest).at(-1)

  const current = () => {
    const snapshot = adapter.getSnapshot()
    const item = snapshot.objectives.find(o => o.id === objective.id)

    return snapshot.connection?.state === 'ready' && snapshot.connection.scope === initialScope.current &&
      snapshot.connection.ownerScope === initialOwnerScope.current && item?.acceptance?.contract?.sha256 === contract.sha256 &&
      item.acceptance.deliverableSha256 === digest
  }

  const available = !!expectedOrganizationId && !!adapter.recordOutcomeJudgment && current() &&
    objective.acceptance?.status === 'reviewing' && !!digest && /^[a-f0-9]{64}$/.test(digest)

  const save = async (approved: boolean) => {
    if (lock.current || !expectedOrganizationId || (intent && intent.expectedOrganizationId !== expectedOrganizationId) || !current() || !adapter.recordOutcomeJudgment || (!intent && (!available || !confirmed || !reason.trim()))) {return}

    const request = intent ?? { expectedOrganizationId: expectedOrganizationId!, objectiveId: objective.id, criterionId,
      contractSha256: contract.sha256, deliverableSha256: digest!, approved, reason: reason.trim(),
      idempotencyKey: crypto.randomUUID(), confirmed: true as const }

    lock.current = true; setBusy(true); setIntent(request); setError('')

    try {
      await adapter.recordOutcomeJudgment(request)

      if (current()) {setMessage(copy.saved); setIntent(null); setConfirmed(false)}
    } catch (e) {if (current()) {setError(e instanceof Error ? e.message : copy.unavailable)}}
    finally {lock.current = false; setBusy(false)}
  }

  return <section aria-label={criterion.text}><h3>{criterion.text}</h3>
    {latest && <p>{latest.approved ? copy.approve : copy.reject}: {latest.reason}</p>}
    {message && <p role="status">{message}</p>}{error && <p role="alert">{error}</p>}
    {available || intent ? <><label>{copy.reason}<Textarea disabled={busy || !!intent} maxLength={2000} onChange={e => setReason(e.target.value)} value={reason} /></label>
      <label><input checked={confirmed} disabled={busy || !!intent} onChange={e => setConfirmed(e.target.checked)} type="checkbox" />{copy.confirm}</label>
      {intent ? <Button disabled={busy || !expectedOrganizationId || expectedOrganizationId !== intent.expectedOrganizationId || !current()} onClick={() => void save(intent.approved)}>{copy.retry}</Button> :
        <div className="eid-actions"><Button disabled={busy || !confirmed || !reason.trim()} onClick={() => void save(true)}>{copy.approve}</Button>
          <Button disabled={busy || !confirmed || !reason.trim()} onClick={() => void save(false)} variant="secondary">{copy.reject}</Button></div>}
    </> : <p className="eid-note">{copy.unavailable}</p>}
  </section>
}
