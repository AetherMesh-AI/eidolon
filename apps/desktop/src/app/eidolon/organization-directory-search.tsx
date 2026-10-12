import './organization-directory.css'

import { useEffect, useId, useRef, useState, useSyncExternalStore } from 'react'

import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/components/ui/dialog'
import { SearchField } from '@/components/ui/search-field'
import type { DirectoryActivationCopy } from '@/i18n/organization-directory-activation'

import type { OrganizationDirectoryEntry } from './organization-directory'
import { type OrganizationDirectory, searchOrganizationDirectory } from './organization-directory'
import type { ActivationResult, createOrganizationDirectoryActivation } from './organization-directory-activation'

export interface OrganizationDirectoryCopy {
  heading: string
  description: string
  search: string
  refresh: string
  empty: string
  lastKnown: string
  verified: string
  unavailable: string
  unnamed: string
  unconfigured: string
  unsupported: string
  choose?: string
  inspectionOnly?: string
}

/** Highlighting is inspection. Only a separate confirmation may admit selection. */
export function OrganizationDirectorySearch({
  directory,
  activation,
  activationCopy,
  copy,
  isCurrent,
  onClose,
  onReturnFocus
}: {
  directory: OrganizationDirectory
  activation?: ReturnType<typeof createOrganizationDirectoryActivation>
  activationCopy?: DirectoryActivationCopy
  copy: OrganizationDirectoryCopy
  isCurrent: () => boolean
  onClose: () => void
  onReturnFocus: () => void
}) {
  const view = useSyncExternalStore(directory.subscribe, directory.getSnapshot)
  const [query, setQuery] = useState('')
  const [highlight, setHighlight] = useState<string | null>(null)
  const [prepared, setPrepared] = useState<OrganizationDirectoryEntry | null>(null)
  const [pending, setPending] = useState(false)
  const [outcome, setOutcome] = useState<ActivationResult['status'] | null>(null)
  const admitted = useRef(false)
  const returnToChoice = useRef(false)

  const cancel = () => {
    if (pending || !activation?.cancel()) {
      return
    }

    returnToChoice.current = true
    setPrepared(null)
  }

  const close = () => {
    if (pending) {
      return
    }

    activation?.cancel()
    onClose()
  }

  const id = useId()
  const results = searchOrganizationDirectory(view.entries, query)
  const selected = results.findIndex(entry => entry.key === highlight)
  useEffect(() => {
    if (!isCurrent() && !admitted.current) {
      onClose()
    }
  }, [view, isCurrent, onClose])

  return (
    <Dialog
      onOpenChange={open => {
        if (!open) {
          close()
        }
      }}
      open
    >
      <DialogContent
        aria-busy={pending}
        className="eidolon eid-organization-directory"
        onCloseAutoFocus={event => {
          event.preventDefault()

          if (isCurrent()) {
            onReturnFocus()
          }
        }}
        onEscapeKeyDown={event => {
          event.preventDefault()
          event.stopPropagation()

          if (prepared) {
            cancel()
          } else {
            close()
          }
        }}
        showCloseButton={!pending && !outcome}
      >
        <DialogTitle>{copy.heading}</DialogTitle>
        <DialogDescription>{copy.description}</DialogDescription>
        {!prepared && !outcome && (
          <>
            <div>
              <SearchField
                aria-label={copy.search}
                onChange={value => {
                  setQuery(value)
                  setHighlight(null)
                }}
                placeholder={copy.search}
                value={query}
              />
              <ul
                aria-activedescendant={selected >= 0 ? `${id}-${selected}` : undefined}
                aria-label={copy.heading}
                onKeyDown={event => {
                  if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key) || !results.length) {
                    return
                  }

                  event.preventDefault()

                  const next =
                    event.key === 'Home'
                      ? 0
                      : event.key === 'End'
                        ? results.length - 1
                        : event.key === 'ArrowDown'
                          ? (selected + 1) % results.length
                          : selected < 0
                            ? results.length - 1
                            : (selected + results.length - 1) % results.length

                  setHighlight(results[next].key)
                }}
                role="listbox"
                tabIndex={0}
              >
                {results.map((entry, index) => (
                  <li
                    aria-selected={selected === index}
                    id={`${id}-${index}`}
                    key={entry.key}
                    onClick={() => setHighlight(entry.key)}
                    role="option"
                  >
                    <span>
                      {entry.name ??
                        (entry.state === 'unconfigured'
                          ? copy.unconfigured
                          : entry.state === 'unsupported'
                            ? copy.unsupported
                            : copy.unnamed)}
                    </span>
                    <span>
                      {' '}
                      · {entry.connectionLabel} · {entry.profile}
                    </span>
                    <small> · {entry.freshness === 'fresh' ? copy.verified : copy.lastKnown}</small>
                  </li>
                ))}
              </ul>
            </div>
            {copy.choose && (
              <Button
                disabled={
                  !activation ||
                  !activationCopy ||
                  selected < 0 ||
                  !isCurrent() ||
                  results[selected]?.state === 'unsupported'
                }
                onClick={() => {
                  const entry = results[selected]

                  if (entry && activation?.prepare(entry.key)) {
                    setPrepared({ ...entry })
                  }
                }}
                ref={node => {
                  if (node && returnToChoice.current) {
                    returnToChoice.current = false
                    node.focus()
                  }
                }}
              >
                {copy.choose}
              </Button>
            )}
            {!activation && copy.inspectionOnly && <p>{copy.inspectionOnly}</p>}
            {!results.length && <p role="status">{copy.empty}</p>}
            {view.error && <p role="status">{copy.unavailable}</p>}
            <Button
              disabled={view.refreshing || !isCurrent()}
              onClick={() => {
                if (isCurrent()) {
                  void directory.refresh()
                }
              }}
              variant="ghost"
            >
              {copy.refresh}
            </Button>
          </>
        )}
        {prepared && activationCopy && (
          <section aria-label={activationCopy.confirm}>
            <p>{activationCopy.explanation}</p>
            <p>
              {prepared.name ?? copy.unnamed} · {prepared.connectionLabel} · {prepared.profile}
            </p>
            {pending ? (
              <p role="status">{activationCopy.pending}</p>
            ) : (
              <>
                <Button autoFocus onClick={cancel} variant="ghost">
                  {activationCopy.cancel}
                </Button>
                <Button
                  onClick={async () => {
                    if (!activation || admitted.current) {
                      return
                    }

                    admitted.current = true
                    setPending(true)

                    try {
                      const answer = await activation.confirm()
                      setOutcome(answer.status)
                    } catch {
                      // Publication may already have happened. Never claim rollback.
                      setOutcome('committed-unverified')
                    } finally {
                      setPrepared(null)
                      setPending(false)
                    }
                  }}
                >
                  {activationCopy.confirm}
                </Button>
              </>
            )}
          </section>
        )}
        {outcome && activationCopy && (
          <>
            <p role="status">{activationCopy[outcome]}</p>
            <Button autoFocus onClick={close}>
              {activationCopy.close}
            </Button>
          </>
        )}
      </DialogContent>
    </Dialog>
  )
}
