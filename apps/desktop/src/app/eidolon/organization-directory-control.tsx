import { useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { useLocation, useNavigate } from 'react-router'

import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { useI18n } from '@/i18n/context'
import { organizationCreationCopy } from '@/i18n/organization-creation'
import { organizationDirectoryCopy } from '@/i18n/organization-directory'
import { directoryActivationCopy } from '@/i18n/organization-directory-activation'
import { setSettingsScope } from '@/store/settings-scope'

import type { OrganizationCreationClient, OrganizationModelSetupClient } from './organization-creation-client'
import { OrganizationCreationDialog } from './organization-creation-dialog'
import {
  createRuntimeOrganizationCreation,
  createRuntimeOrganizationModelSetup,
  directoryActivationVerifier
} from './organization-creation-runtime'
import { createOrganizationDirectoryActivation } from './organization-directory-activation'
import {
  createRuntimeOrganizationDirectory,
  subscribeOrganizationDirectorySource
} from './organization-directory-runtime'
import { OrganizationDirectorySearch } from './organization-directory-search'
import { OrganizationModelSetup } from './organization-model-setup'
import { readOrganizationMetadataScope } from './use-organization-metadata'

export function OrganizationDirectoryControl({
  label,
  organizationName
}: {
  label?: string
  organizationName?: string
}) {
  const { locale, t } = useI18n()
  const navigate = useNavigate()
  const location = useLocation()
  const [creation, setCreation] = useState<OrganizationCreationClient | null>(null)
  const creationRef = useRef<OrganizationCreationClient | null>(null)
  const [modelSetup, setModelSetup] = useState<OrganizationModelSetupClient | null>(null)
  const modelSetupRef = useRef<OrganizationModelSetupClient | null>(null)

  const initials = organizationName
    ?.trim()
    .split(/\s+/)
    .slice(0, 2)
    .map(part => Array.from(part)[0])
    .join('')
    .toLocaleUpperCase(locale)

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
      modelSetupRef.current?.dispose()
      creationRef.current?.dispose()
      runtimeRef.current?.directory.dispose()
      runtimeRef.current = null
    },
    []
  )
  // Dispose owned clients on navigation; these refs are handles, not mirrored reactive values.
  // eslint-disable-next-line no-restricted-syntax
  useEffect(() => {
    modelSetupRef.current?.dispose()
    modelSetupRef.current = null
    setModelSetup(null)
    creationRef.current?.dispose()
    creationRef.current = null
    setCreation(null)
    setOrigin(null)
  }, [location.key])
  const runtime = runtimeRef.current

  const current = () => {
    const source = runtimeRef.current?.readSource().current

    return Boolean(source?.scope.eligible && JSON.stringify([source.scope.key, source.scope.ownerKey]) === origin)
  }

  const closeCreation = (restoreFocus = true) => {
    const client = creationRef.current
    const focus = restoreFocus && (client?.isCurrent() || client?.canSetup())
    client?.dispose()
    creationRef.current = null
    setCreation(null)

    if (focus) {
      queueMicrotask(() => {
        if (trigger.current?.isConnected && !trigger.current.disabled) {
          trigger.current.focus()
        }
      })
    }
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
                enabled: () => true,
                verifyDestination: directoryActivationVerifier
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
        <span aria-hidden="true" className="eid-workspace-organization-monogram">
          {initials || <Codicon name="organization" />}
        </span>
        <span className="eid-workspace-organization-copy">
          <span className="eid-workspace-organization-name" dir="auto">
            {label ?? copy.heading}
          </span>
          <small aria-hidden="true">{t.organizationHome.organization}</small>
        </span>
        <Codicon name="chevron-down" />
      </Button>
      {origin && runtime && (
        <OrganizationDirectorySearch
          activation={runtime.activation}
          activationCopy={directoryActivationCopy[locale]}
          copy={copy}
          createLabel={organizationCreationCopy[locale].title}
          directory={runtime.directory}
          isCurrent={current}
          modelSetupLabel={organizationCreationCopy[locale].setupModel}
          onClose={() => setOrigin(null)}
          onCreate={() => {
            if (!current()) {
              return
            }

            const client = createRuntimeOrganizationCreation(runtime)
            creationRef.current = client
            setCreation(client)
            setOrigin(null)
          }}
          onModelSetup={() => {
            if (!current()) {
              return
            }

            const client = createRuntimeOrganizationModelSetup(runtime)
            modelSetupRef.current = client
            setModelSetup(client)
            setOrigin(null)
          }}
          onReturnFocus={() => {
            if (
              !creationRef.current &&
              !modelSetupRef.current &&
              current() &&
              trigger.current?.isConnected &&
              !trigger.current.disabled
            ) {
              trigger.current.focus()
            }
          }}
        />
      )}
      {modelSetup && (
        <OrganizationModelSetup
          client={modelSetup}
          onClose={() => {
            const focus = modelSetup.isCurrent()
            modelSetup.dispose()
            modelSetupRef.current = null
            setModelSetup(null)

            if (focus) {
              queueMicrotask(() => {
                if (trigger.current?.isConnected && !trigger.current.disabled) {
                  trigger.current.focus()
                }
              })
            }
          }}
        />
      )}
      {creation && (
        <OrganizationCreationDialog
          client={creation}
          onClose={() => closeCreation()}
          onSetup={profile => {
            if (!creation.canSetup()) {
              return
            }

            setSettingsScope(profile)
            closeCreation(false)
            navigate('/settings?tab=providers&pview=keys')
          }}
        />
      )}
    </>
  )
}
