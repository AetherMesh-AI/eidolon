import { useEffect, useRef, useState, useSyncExternalStore } from 'react'

import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { useI18n } from '@/i18n/context'
import { organizationDirectoryCopy } from '@/i18n/organization-directory'
import { directoryActivationCopy } from '@/i18n/organization-directory-activation'

import { createOrganizationDirectoryActivation } from './organization-directory-activation'
import {
  createRuntimeOrganizationDirectory,
  subscribeOrganizationDirectorySource
} from './organization-directory-runtime'
import { OrganizationDirectorySearch } from './organization-directory-search'
import { readOrganizationMetadataScope } from './use-organization-metadata'

/** Production stays inspection-only; the explicit acceptance build tests the actual door. */
const selectionEnabled = import.meta.env.MODE === 'organization-directory-acceptance'

export function OrganizationDirectoryControl({ label, organizationName }: { label?: string; organizationName?: string }) {
  const { locale, t } = useI18n()
  const initials = organizationName?.trim().split(/\s+/).slice(0, 2).map(part => Array.from(part)[0]).join('').toLocaleUpperCase(locale)

  const eligible = useSyncExternalStore(
    subscribeOrganizationDirectorySource,
    () => readOrganizationMetadataScope().eligible
  )

  const copy = organizationDirectoryCopy[locale]

  const runtimeRef = useRef<
    | (ReturnType<typeof createRuntimeOrganizationDirectory> & {
        activation: ReturnType<typeof createOrganizationDirectoryActivation>
      })
    | null
  >(null)

  const [origin, setOrigin] = useState<string | null>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  useEffect(
    () => () => {
      runtimeRef.current?.directory.dispose()
      runtimeRef.current = null
    },
    []
  )
  const runtime = runtimeRef.current

  const current = () => {
    const source = runtimeRef.current?.readSource().current

    return Boolean(source?.scope.eligible && JSON.stringify([source.scope.key, source.scope.ownerKey]) === origin)
  }

  return (
    <>
      <Button
        aria-label={label ? `${label} · ${copy.heading}` : copy.heading}
        className="eid-workspace-organization-trigger"
        disabled={!eligible}
        onClick={() => {
          if (!runtimeRef.current) {
            const next = createRuntimeOrganizationDirectory()
            runtimeRef.current = {
              ...next,
              activation: createOrganizationDirectoryActivation({
                ...next,
                enabled: () => selectionEnabled
              })
            }
          }

          const source = runtimeRef.current?.readSource().current

          if (source?.scope.eligible) {
            setOrigin(JSON.stringify([source.scope.key, source.scope.ownerKey]))
          }
        }}
        ref={trigger}
        size="lg"
        variant="ghost"
      >
        <span aria-hidden="true" className="eid-workspace-organization-monogram">{initials || <Codicon name="organization" />}</span>
        <span className="eid-workspace-organization-copy"><span className="eid-workspace-organization-name" dir="auto">{label ?? copy.heading}</span><small aria-hidden="true">{t.organizationHome.organization}</small></span>
        <Codicon name="chevron-down" />
      </Button>
      {origin && runtime && (
        <OrganizationDirectorySearch
          activation={selectionEnabled ? runtime.activation : undefined}
          activationCopy={directoryActivationCopy[locale]}
          copy={copy}
          directory={runtime.directory}
          isCurrent={current}
          onClose={() => setOrigin(null)}
          onReturnFocus={() => {
            if (current() && trigger.current?.isConnected && !trigger.current.disabled) {
              trigger.current.focus()
            }
          }}
        />
      )}
    </>
  )
}
