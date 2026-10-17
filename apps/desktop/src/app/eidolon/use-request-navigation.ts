import { useEffect, useState } from 'react'

import { captureInspectorOrigin, type InspectorOrigin } from './inspector'
import type { OrganizationRequest } from './types'

interface Selection {
  scope: string
  objectiveId: string
  entries: { id: string; origin: InspectorOrigin | null }[]
}

/** Keep response forms mounted while inspecting ancestors; scope owns the trail. */
export function useRequestNavigation(requests: OrganizationRequest[], scope: string) {
  const [selection, setSelection] = useState<Selection | null>(null)
  const rows = selection?.entries.map(entry => requests.find(request => request.id === entry.id)) ?? []

  const valid =
    selection?.scope === scope &&
    rows.every(
      (row, index) =>
        row && row.objectiveId === selection.objectiveId && (index === 0 || rows[index - 1]?.parentRequestId === row.id)
    )

  const trail = valid ? (rows as OrganizationRequest[]) : []

  // Discard invalid navigation so a later snapshot cannot reopen stale details.
  useEffect(() => {
    if (selection && !valid) {
      setSelection(null)
    }
  }, [selection, valid])

  const open = (id: string) => {
    const request = requests.find(row => row.id === id)

    if (request) {
      setSelection({ scope, objectiveId: request.objectiveId, entries: [{ id, origin: captureInspectorOrigin(document.activeElement) }] })
    }
  }

  const current = trail.at(-1)

  const parent = current
    ? requests.find(
        row =>
          row.id === current.parentRequestId &&
          row.objectiveId === current.objectiveId &&
          !selection?.entries.some(entry => entry.id === row.id)
      )
    : undefined

  return {
    trail,
    origins: valid ? selection.entries.map(entry => entry.origin) : [],
    parent,
    open,
    close: () => setSelection(null),
    // Async response completion may close only the navigation that submitted it.
    // Every user navigation creates a new selection, including Back to the same request.
    closeOnResponse: () => setSelection(current => (current === selection ? null : current)),
    back: () => setSelection(selection && valid ? { ...selection, entries: selection.entries.slice(0, -1) } : null),
    openParent: () => {
      if (selection && valid && parent) {
        // Capture before the retained child becomes hidden and unavailable.
        setSelection({ ...selection, entries: [...selection.entries, { id: parent.id, origin: captureInspectorOrigin(document.activeElement) }] })
      }
    }
  }
}
