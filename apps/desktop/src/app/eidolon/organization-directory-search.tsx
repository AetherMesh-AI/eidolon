import './organization-directory.css'

import { useEffect, useId, useState, useSyncExternalStore } from 'react'

import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/components/ui/dialog'
import { SearchField } from '@/components/ui/search-field'

import { type OrganizationDirectory, searchOrganizationDirectory } from './organization-directory'

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

/** Unmounted preparation surface. Highlighting is inspection, never activation. */
export function OrganizationDirectorySearch({
  directory,
  copy,
  isCurrent,
  onClose,
  onReturnFocus
}: {
  directory: OrganizationDirectory
  copy: OrganizationDirectoryCopy
  isCurrent: () => boolean
  onClose: () => void
  onReturnFocus: () => void
}) {
  const view = useSyncExternalStore(directory.subscribe, directory.getSnapshot)
  const [query, setQuery] = useState('')
  const [highlight, setHighlight] = useState<string | null>(null)
  const id = useId()
  const results = searchOrganizationDirectory(view.entries, query)
  const selected = results.findIndex(entry => entry.key === highlight)
  useEffect(() => {
    if (!isCurrent()) {
      onClose()
    }
  }, [view, isCurrent, onClose])

  return (
    <Dialog
      onOpenChange={open => {
        if (!open) {
          onClose()
        }
      }}
      open
    >
      <DialogContent
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
          onClose()
        }}
      >
        <DialogTitle>{copy.heading}</DialogTitle>
        <DialogDescription>{copy.description}</DialogDescription>
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
        {copy.choose && <Button disabled>{copy.choose}</Button>}
        {copy.inspectionOnly && <p>{copy.inspectionOnly}</p>}
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
      </DialogContent>
    </Dialog>
  )
}
