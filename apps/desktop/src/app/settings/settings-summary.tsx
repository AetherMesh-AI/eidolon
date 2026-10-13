import { useStore } from '@nanostores/react'

import { useI18n } from '@/i18n'
import { $settingsRequestProfile } from '@/store/settings-scope'
import { useTheme } from '@/themes/context'
import type { HermesConfigRecord } from '@/types/eidolon'

import { useHermesConfigRecord } from '../hooks/use-config-record'

import { FIELD_LABELS } from './constants'
import { fieldCopyForSchemaKey } from './field-copy'
import { getNested } from './helpers'

type SummarySection = 'providers' | 'safety' | 'workspace'

/** Fixed non-secret fields only. A configured provider is not a verified connection. */
export function SettingsConfigSummary({ section, record, loading = false, failed = false }: {
  section: SummarySection
  record?: HermesConfigRecord
  loading?: boolean
  failed?: boolean
}) {
  const { t } = useI18n()
  const missing = t.organizationSurfaces.objectiveRecord.notRecorded

  const keys = {
    providers: ['model.provider', 'model.default'],
    safety: ['approvals.mode', 'approvals.timeout', 'security.redact_secrets'],
    workspace: ['terminal.cwd', 'terminal.backend', 'terminal.timeout']
  }[section]

  if (loading && !record) {
    return <p role="status">{t.common.loading}</p>
  }

  if (failed) {
    return <p role="status">{t.errors.genericFailure}</p>
  }

  return <dl className="eid-settings-summary">
    {keys.map(key => {
      const value = key === 'model.default' && typeof record?.model === 'string' ? record.model : getNested(record ?? {}, key)
      const label = key === 'model.provider' ? t.organizationRuntime.provider : key === 'model.default' ? t.organizationRuntime.model : fieldCopyForSchemaKey(t.settings.fieldLabels, key) ?? fieldCopyForSchemaKey(FIELD_LABELS, key) ?? key
      const scalar = typeof value === 'string' ? value.trim() : typeof value === 'boolean' || typeof value === 'number' && Number.isFinite(value) ? String(value) : ''

      return <div key={key}><dt>{label}</dt><dd dir="auto">{scalar || missing}</dd></div>
    })}
  </dl>
}

export function SavedSettingsSummary({ section }: { section: SummarySection }) {
  const profile = useStore($settingsRequestProfile)
  const { data, isPending, isError } = useHermesConfigRecord(profile)

  return <SettingsConfigSummary failed={isError} loading={isPending} record={data} section={section} />
}

export function AppearanceSummary() {
  const { t, locale } = useI18n()
  const { themeName, mode, availableThemes } = useTheme()
  const theme = availableThemes.find(item => item.name === themeName)

  return <dl className="eid-settings-summary">
    <div><dt>{t.settings.appearance.title}</dt><dd>{theme?.label ?? themeName} · {mode}</dd></div>
    <div><dt>{t.language.label}</dt><dd>{new Intl.DisplayNames([locale], { type: 'language' }).of(locale)}</dd></div>
  </dl>
}
