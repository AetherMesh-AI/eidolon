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

export function OrganizationDirectoryControl() {
  const { locale } = useI18n()

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
        aria-label={copy.heading}
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
        size="micro"
        variant="ghost"
      >
        <Codicon name="organization" />
        <span className="eid-workspace-action-label">{copy.heading}</span>
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
