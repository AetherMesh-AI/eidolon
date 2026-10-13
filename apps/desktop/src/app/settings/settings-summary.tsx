import { useStore } from '@nanostores/react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'

import { getApiRequestConnection, getHermesConfigRecord } from '@/eidolon'
import { useI18n } from '@/i18n'
import { $pendingConnectionId } from '@/store/connections'
import { $activeGatewayProfile, $gatewaySwapTarget } from '@/store/profile'
import { $connection } from '@/store/session'
import { $settingsRequestProfile } from '@/store/settings-scope'
import { useTheme } from '@/themes/context'
import type { HermesConfigRecord } from '@/types/eidolon'

import { HERMES_CONFIG_KEY, hermesConfigKey } from '../hooks/use-config-record'

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
  const requestedProfile = useStore($settingsRequestProfile)
  const activeProfile = useStore($activeGatewayProfile)
  const connection = useStore($connection)
  const pending = useStore($pendingConnectionId)
  const swapping = useStore($gatewaySwapTarget)
  const client = useQueryClient()
  const profile = requestedProfile ?? activeProfile
  // Capture the REST route and non-secret descriptor identity in both the read
  // and its cache owner. A late prior-owner response can only populate its own key.
  const connectionId = getApiRequestConnection()
  const owner = JSON.stringify([connectionId, connection?.baseUrl, connection?.remoteIdentity, connection?.authenticationIdentity, connection?.oauthSessionIdentity, profile])
  const enabled = !!connection && !pending && !swapping

  const { data, isPending, isError } = useQuery({
    queryKey: [...HERMES_CONFIG_KEY, 'saved-summary', owner],
    queryFn: () => getHermesConfigRecord({ connectionId, profile }),
    enabled,
    staleTime: 0
  })

  // Existing editors keep their cache and draft ownership. Their write-through
  // only requests an independent read from this summary's captured owner.
  useEffect(() => client.getQueryCache().subscribe(event => {
    if (enabled && event.type === 'updated' && event.action.type === 'success' && event.action.manual && JSON.stringify(event.query.queryKey) === JSON.stringify(hermesConfigKey(requestedProfile))) {
      void client.invalidateQueries({ queryKey: [...HERMES_CONFIG_KEY, 'saved-summary', owner], exact: true }, { cancelRefetch: false })
    }
  }), [client, enabled, owner, requestedProfile])

  return <SettingsConfigSummary failed={enabled && isError} loading={!enabled || isPending} record={enabled ? data : undefined} section={section} />
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
