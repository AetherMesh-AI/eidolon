import './eidolon.css'

import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty-state'
import { useI18n } from '@/i18n/context'

import { demoSnapshot } from './demo'
import type { OrganizationSnapshot } from './types'

export const legacyOrganizationKey = 'eidolon.organization.v1'
export interface LegacyOrganizationRecord {
  raw: string | null
  snapshot: OrganizationSnapshot | null
  unavailable: boolean
}

/** Read only: even malformed records remain byte-for-byte exportable. */
export function readLegacyOrganization(storage: Pick<Storage, 'getItem'>): LegacyOrganizationRecord {
  let raw: string | null

  try {
    raw = storage.getItem(legacyOrganizationKey)
  } catch {
    return { raw: null, snapshot: null, unavailable: true }
  }

  if (raw === null) {
    return { raw, snapshot: null, unavailable: false }
  }

  try {
    const snapshot = JSON.parse(raw) as OrganizationSnapshot

    if (
      !snapshot ||
      !['objectives', 'agents', 'tasks', 'activity', 'knowledge'].every(key =>
        Array.isArray(snapshot[key as keyof OrganizationSnapshot])
      ) ||
      snapshot.objectives.some(
        item =>
          !item ||
          typeof item.id !== 'string' ||
          typeof item.title !== 'string' ||
          typeof item.description !== 'string' ||
          (item.result !== undefined && typeof item.result !== 'string')
      )
    ) {
      return { raw, snapshot: null, unavailable: false }
    }

    return { raw, snapshot, unavailable: false }
  } catch {
    return { raw, snapshot: null, unavailable: false }
  }
}

export function exportLegacyOrganization(record: LegacyOrganizationRecord) {
  if (record.raw === null) {
    return
  }

  const url = URL.createObjectURL(new Blob([record.raw], { type: 'application/json;charset=utf-8' }))
  const link = document.createElement('a')
  link.href = url
  link.download = 'eidolon-organization-legacy-original.json'
  link.click()
  setTimeout(() => URL.revokeObjectURL(url), 0)
}

export function LegacyOrganizationHistory({ storage }: { storage?: Pick<Storage, 'getItem'> }) {
  const { t } = useI18n()
  const copy = t.organizationWork

  const [record] = useState(() => {
    try {
      return readLegacyOrganization(storage ?? window.localStorage)
    } catch {
      return { raw: null, snapshot: null, unavailable: true }
    }
  })

  const [example] = useState(demoSnapshot)

  const renderRecords = (snapshot: OrganizationSnapshot) => (
    <ol className="eid-list">
      {snapshot.objectives.map(item => (
        <li key={item.id}>
          <h3>{item.title}</h3>
          <p className="eid-result-text">{item.description}</p>
          {item.result && (
            <>
              <h4>{copy.result}</h4>
              <p className="eid-result-text">{item.result}</p>
            </>
          )}
        </li>
      ))}
    </ol>
  )

  return (
    <main className="eidolon eid-workspace">
      <div className="eid-page">
        <h1>{copy.legacyTitle}</h1>
        <p>{copy.legacyNote}</p>
        {record.raw !== null && (
          <Button onClick={() => exportLegacyOrganization(record)} variant="secondary">
            {copy.legacyExport}
          </Button>
        )}
        {record.unavailable ? (
          <p role="alert">{copy.legacyUnavailable}</p>
        ) : record.raw === null ? (
          <EmptyState title={copy.legacyEmpty} />
        ) : !record.snapshot ? (
          <p role="alert">{copy.legacyInvalid}</p>
        ) : (
          renderRecords(record.snapshot)
        )}
        <details>
          <summary>{copy.legacyDemo}</summary>
          {renderRecords(example)}
        </details>
      </div>
    </main>
  )
}
