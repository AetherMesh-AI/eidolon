import type { Locale } from '@/i18n/types'

const en = {
  title: 'Prepared validation for this new objective',
  enable: 'Use explicitly selected prepared validation registrations',
  activation: 'Enabling this mode sets managed-artifact delivery and requires TypeScript and project tests. Selecting a v2 registration also requires the renderer build. It removes source integration. Select every objective project explicitly below, up to eight; availability alone grants nothing.',
  disabling: 'Disabling removes prepared references and the mode-only TypeScript check and any renderer-build check added by this mode. Delivery and other chosen checks remain editable below.',
  read: 'Read available prepared registrations',
  refresh: 'Reread and review prepared registrations',
  reading: 'Reading prepared registrations',
  choose: 'Use this exact registration',
  revision: 'Registration revision',
  hash: 'Registration SHA-256',
  projectTypecheck: 'Project TypeScript check',
  projectBuild: 'Project renderer build check',
  boundaries: 'Linux x64 with pinned Docker only. Runtime readiness has not been probed. A v1 validation set uses two execution units; a v2 set uses three. Existing grants and owner/objective limits still apply. This does not allow arbitrary commands, network access, package installation, or edits to a project with recipe: null.',
  v1: 'Profile v1: TypeScript and UI tests; two execution units',
  v2: 'Profile v2: TypeScript, UI tests, and renderer build; three execution units',
  versionNote: 'Choose the same profile version for every project in this objective. A v2 build retains bounded output-manifest metadata with outputBodiesRetained: false. It does not deliver a deployable artifact, publish source, or package Electron.',
  versions: 'Choose registrations with the same profile version for every selected project. All projects must satisfy the same required checks.',
  newOnly: 'Availability is a read snapshot; the backend rechecks the exact registration when creating the objective. These references apply only to this new objective. Existing objectives do not adopt them. Replacing or revoking a registration can make this exact revision unavailable.',
  unavailable: 'A current authenticated owner connection matching this objective is required to read prepared registrations.',
  changed: 'The owner, profile, connection, project binding, or registration review changed. Prepared mode is still requested, but its selections are cleared. Reread and select again, or explicitly disable prepared mode.',
  incomplete: 'Prepared mode requires an exact registration for every selected project, up to eight projects. Read availability and check each registration explicitly.',
  constraints: 'Prepared mode requires managed-artifact delivery, TypeScript and project tests, the renderer build for v2 only, and no source integration.',
  empty: 'No currently selectable registrations match the selected objective projects.',
  selectProjects: 'Select the objective projects above before choosing their registrations.',
  failed: 'Prepared registration availability could not be verified. Reread before selecting.',
  capacity: 'New registrations cannot be saved at capacity; existing current selections remain available.',
  unsupported: 'Prepared validation is unavailable on this backend platform or is blocked by its current configuration.',
  pending: 'Wait for the explicit availability read before continuing.',
  selected: 'Choose exact references for this new objective',
  none: 'No prepared references selected.'
}

// Organization foundation copy uses the reviewed English fallback until translations are reviewed.
export const preparedObjectiveCopy: Record<Locale, typeof en> = { en, ja: en, zh: en, 'zh-hant': en, ar: en, ru: en }
