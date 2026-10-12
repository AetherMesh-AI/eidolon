import { translateNow } from '@/i18n/runtime'

import { validAttentionPage } from './runtime-attention-types'
import { normalizeConversations } from './runtime-conversation-contract'
import { validOutcomePage } from './runtime-outcome-types'
import { validOrganizationSetup } from './runtime-setup-contract'
import { validWorkPackages } from './runtime-work-package-contract'
import type { OrganizationSnapshot } from './types'

export function validateSnapshot(value: OrganizationSnapshot): OrganizationSnapshot {
  if (!value || value.source !== 'runtime' || !['objectives', 'agents', 'tasks', 'activity', 'knowledge', 'requests'].every(field => Array.isArray(value[field as keyof OrganizationSnapshot])) || !value.runtime) {
    throw new Error('This backend did not return a supported organization snapshot. Update the runtime and reconnect.')
  }

  if (!validWorkPackages(value)) {throw new Error(translateNow('organizationWork.packageInvalid'))}

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

