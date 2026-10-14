import { adaptivePacingCopy } from '@/i18n/organization-adaptive-pacing'
import { completionReserveCopy } from '@/i18n/organization-completion-reserve'
import { modelRoutingCopy } from '@/i18n/organization-model-routing'
import { getRuntimeI18nLocale, translateNow } from '@/i18n/runtime'

import { validAdaptivePacing } from './runtime-adaptive-pacing-contract'
import { validAttentionPage } from './runtime-attention-types'
import { validCompletionReserve } from './runtime-completion-reserve-contract'
import { normalizeConversations } from './runtime-conversation-contract'
import { validSnapshotModelRouting } from './runtime-model-routing-contract'
import { validOutcomePage } from './runtime-outcome-types'
import { validOrganizationSetup } from './runtime-setup-contract'
import { validWorkPackages } from './runtime-work-package-contract'
import type { OrganizationSnapshot } from './types'

export function validateSnapshot(value: OrganizationSnapshot): OrganizationSnapshot {
  if (!value || value.source !== 'runtime' || !['objectives', 'agents', 'tasks', 'activity', 'knowledge', 'requests'].every(field => Array.isArray(value[field as keyof OrganizationSnapshot])) || !value.runtime) {
    throw new Error('This backend did not return a supported organization snapshot. Update the runtime and reconnect.')
  }

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

