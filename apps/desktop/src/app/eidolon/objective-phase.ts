import type { OrganizationPhaseCopy } from '@/i18n/organization-phases'

import type { Objective } from './types'

/** Older runtimes, future codes and authored prototype text retain exact wording. */
export function objectivePhaseLabel(objective: Objective, labels: OrganizationPhaseCopy) {
  const code = objective.phaseCode

  return objective.source === 'runtime' && typeof code === 'string' && Object.hasOwn(labels, code)
    ? labels[code as keyof OrganizationPhaseCopy]
    : objective.phase
}
