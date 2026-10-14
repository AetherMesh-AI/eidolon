import { useI18n } from '@/i18n/context'
import { modelRoutingCopy } from '@/i18n/organization-model-routing'

import { LocalizedTime } from './localized-time'
import type { OrganizationModelDispatch } from './runtime-model-routing-types'

export function RuntimeModelDispatch({ dispatch }: { dispatch: OrganizationModelDispatch }) {
  const { t, locale } = useI18n()
  const copy = modelRoutingCopy[locale]

  const reasons: Record<string, string> = {
    'owner-fixed-route': copy.reasonFixed,
    'ordered-fixed-fallback': copy.reasonFallback,
    'cheapest-eligible-configured-ceiling': copy.reasonAuto
  }

  const unknown = t.organizationWork.unknownUsage
  const number = (value: number) => new Intl.NumberFormat(locale).format(value)

  return (
    <section aria-label={`${copy.dispatch}: ${dispatch.routeId}`}>
      <h4>{copy.dispatch}</h4>
      <p className="eid-note">{copy.dispatchNote}</p>
      <dl className="eid-runtime-facts">
        <dt>{copy.dispatchId}</dt><dd><bdi>{dispatch.id}</bdi></dd>
        <dt>{copy.route}</dt><dd><bdi>{dispatch.routeId}</bdi></dd>
        <dt>{copy.selectionMode}</dt><dd>{copy[dispatch.mode]}</dd>
        <dt>{copy.selectedProvider}</dt><dd dir="auto">{dispatch.selectedProvider}</dd>
        <dt>{copy.adapterProvider}</dt><dd dir="auto">{dispatch.adapterProvider}</dd>
        <dt>{t.organizationRuntime.model}</dt><dd dir="auto">{dispatch.model}</dd>
        <dt>{copy.reasoning}</dt><dd>{dispatch.reasoning}</dd>
        <dt>{copy.selectionReason}</dt><dd className="eid-result-text">{reasons[dispatch.selectionReason]}<code className="block" dir="ltr">{dispatch.selectionReason}</code></dd>
        <dt>{copy.generation}</dt><dd>{dispatch.policyGeneration}</dd>
        <dt>{copy.evidence}</dt><dd>{dispatch.evidenceLevel}</dd>
        <dt>{copy.minimumEvidence}</dt><dd>{dispatch.minimumEvidence}</dd>
        <dt>{copy.contextOutput}</dt><dd>{number(dispatch.maxContextTokens)} / {number(dispatch.maxOutputTokens)}</dd>
        <dt>{copy.pricing}</dt><dd>{dispatch.pricing.inputUsdPerMillion ?? unknown} / {dispatch.pricing.outputUsdPerMillion ?? unknown}</dd>
        <dt>{copy.attempt}</dt><dd><bdi>{dispatch.attemptToken}</bdi></dd>
      </dl>
      <LocalizedTime value={dispatch.createdAt} />
      <p className="eid-note">{copy.quotaNote}</p>
    </section>
  )
}
