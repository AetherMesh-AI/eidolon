import type { OrganizationWorkCopy } from '@/i18n/organization-work'

/** Display labels only: request identity, filtering and authority keep the recorded type. */
const requestTypeKeys = new Map<string, keyof OrganizationWorkCopy>([
  ['request.question', 'requestQuestion'],
  ['request.decision', 'requestDecision'],
  ['request.permission', 'requestPermission'],
  ['request.hire', 'requestHire'],
  ['request.decompose', 'requestDecompose'],
  ['request.plan', 'requestPlan'],
  ['request.review', 'requestReview'],
  ['request.test_review', 'requestTestReview'],
  ['request.integrate', 'requestIntegrate'],
  ['request.accept', 'requestAccept'],
  ['request.message', 'requestMessage'],
  ['request.apply', 'requestApply'],
  ['request.validate', 'requestValidate'],
  ['request.validation_failed', 'requestValidationFailed'],
  ['request.project_test', 'requestProjectTest'],
  ['request.project_failed', 'requestProjectFailed'],
  ['request.source_integrate', 'requestSourceIntegrate'],
  ['request.merge', 'requestMerge'],
  ['work.draft', 'workDraft'],
  ['work.analyze', 'workAnalyze'],
  ['work.inspect', 'workInspect'],
  ['work.edit', 'workEdit']
])

export function requestTypeLabel(type: string, copy: OrganizationWorkCopy): string {
  const key = requestTypeKeys.get(type)

  return key ? copy[key] : type
}
