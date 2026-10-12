import { useEffect, useRef, useState, useSyncExternalStore } from 'react'

import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { useI18n } from '@/i18n/context'
import { organizationDirectoryCopy } from '@/i18n/organization-directory'

import {
  createRuntimeOrganizationDirectory,
  subscribeOrganizationDirectorySource
} from './organization-directory-runtime'
import { OrganizationDirectorySearch } from './organization-directory-search'
import { readOrganizationMetadataScope } from './use-organization-metadata'

/** Inspection is available; destination activation remains deliberately unwired. */
export function OrganizationDirectoryControl() {
  const { locale } = useI18n()

  const eligible = useSyncExternalStore(
    subscribeOrganizationDirectorySource,
    () => readOrganizationMetadataScope().eligible
  )

  const copy = organizationDirectoryCopy[locale]
  const runtimeRef = useRef<ReturnType<typeof createRuntimeOrganizationDirectory> | null>(null)
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
          runtimeRef.current ??= createRuntimeOrganizationDirectory()
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
