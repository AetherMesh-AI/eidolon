import { adaptivePacingCopy } from '@/i18n/organization-adaptive-pacing'
import { completionReserveCopy } from '@/i18n/organization-completion-reserve'
import { modelRoutingCopy } from '@/i18n/organization-model-routing'
import { outcomeContractCopy } from '@/i18n/organization-outcome-contracts'
import { selectiveRepairCopy } from '@/i18n/organization-selective-repair'
import { getRuntimeI18nLocale, translateNow } from '@/i18n/runtime'

import { validAdaptivePacing } from './runtime-adaptive-pacing-contract'
import { validAttentionPage } from './runtime-attention-types'
import { validCompletionReserve } from './runtime-completion-reserve-contract'
import { normalizeConversations } from './runtime-conversation-contract'
import { validSnapshotModelRouting } from './runtime-model-routing-contract'
import { validOutcomeContract } from './runtime-outcome-contract-types'
import { validOutcomePage } from './runtime-outcome-types'
import { validOrganizationSetup } from './runtime-setup-contract'
import { validWorkPackages } from './runtime-work-package-contract'
import { validTaskReuse } from './task-reuse-contract'
import type { OrganizationSnapshot } from './types'

export function validateSnapshot(value: OrganizationSnapshot): OrganizationSnapshot {
  if (!value || value.source !== 'runtime' || !['objectives', 'agents', 'tasks', 'activity', 'knowledge', 'requests'].every(field => Array.isArray(value[field as keyof OrganizationSnapshot])) || !value.runtime) {
    throw new Error('This backend did not return a supported organization snapshot. Update the runtime and reconnect.')
  }

  for (const objective of value.objectives) {
    const contract = objective.acceptance?.contract

    if (contract != null && (!validOutcomeContract(contract) || contract.objectiveId !== objective.id ||
      !Array.isArray(objective.acceptance?.criteria) || contract.criteria.length !== objective.acceptance.criteria.length ||
      contract.criteria.some((c, i) => c.text !== objective.acceptance!.criteria[i]))) {
      throw new Error(outcomeContractCopy[getRuntimeI18nLocale()].unavailable)
    }

    const judgments = objective.acceptance?.humanJudgments

    if (judgments !== undefined && (!Array.isArray(judgments) || judgments.some(j => !j || typeof j.criterionId !== 'string' ||
      typeof j.reason !== 'string' || typeof j.approved !== 'boolean' || !/^[a-f0-9]{64}$/.test(j.contractSha256) || !/^[a-f0-9]{64}$/.test(j.deliverableSha256)))) {
      throw new Error(outcomeContractCopy[getRuntimeI18nLocale()].unavailable)
    }
  }
  if (value.tasks.some(task => !validTaskReuse(task))) { throw new Error(selectiveRepairCopy[getRuntimeI18nLocale()].invalid) }

  if (!validSnapshotModelRouting(value)) {throw new Error(modelRoutingCopy[getRuntimeI18nLocale()].invalid)}

  if (!validWorkPackages(value)) {throw new Error(translateNow('organizationWork.packageInvalid'))}

  if (value.objectives.some(objective => objective.usage?.completionReserve !== undefined && !validCompletionReserve(objective.usage.completionReserve))) {
    throw new Error(completionReserveCopy[getRuntimeI18nLocale()].invalid)
  }

  if (value.objectives.some(objective => objective.usage?.adaptivePacing !== undefined && !validAdaptivePacing(objective.usage.adaptivePacing, objective.usage.completionReserve))) {
    throw new Error(adaptivePacingCopy[getRuntimeI18nLocale()].invalid)
  }

  if (value.attention !== undefined && !validAttentionPage(value.attention)) {
    throw new Error(translateNow('organizationWork.attentionInvalid'))
  }

  if (value.outcomes !== undefined && !validOutcomePage(value.outcomes)) {
    throw new Error(translateNow('organizationWork.outcomeInvalid'))
  }

  let conversations: OrganizationSnapshot['conversations']

  try {conversations = normalizeConversations(value.conversations)} catch {throw new Error(translateNow('organizationConversations.invalid'))}

  return { ...value, conversations, runtime: value.runtime.setup !== undefined && !validOrganizationSetup(value.runtime.setup)
    ? { ...value.runtime, setup: undefined }
    : value.runtime }
}

