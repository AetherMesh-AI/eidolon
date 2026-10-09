import { useEffect, useState } from 'react'

import type { OrganizationRequest } from './types'

interface Selection {
  scope: string
  objectiveId: string
  ids: string[]
}

/** Keep response forms mounted while inspecting ancestors; scope owns the trail. */
export function useRequestNavigation(requests: OrganizationRequest[], scope: string) {
  const [selection, setSelection] = useState<Selection | null>(null)
  const rows = selection?.ids.map(id => requests.find(request => request.id === id)) ?? []

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
      setSelection({ scope, objectiveId: request.objectiveId, ids: [id] })
    }
  }

  const current = trail.at(-1)

  const parent = current
    ? requests.find(
        row =>
          row.id === current.parentRequestId &&
          row.objectiveId === current.objectiveId &&
          !selection?.ids.includes(row.id)
      )
    : undefined

  return {
    trail,
    parent,
    open,
    close: () => setSelection(null),
    back: () => setSelection(selection && valid ? { ...selection, ids: selection.ids.slice(0, -1) } : null),
    openParent: () => {
      if (selection && valid && parent) {
        setSelection({ ...selection, ids: [...selection.ids, parent.id] })
      }
    }
  }
}
