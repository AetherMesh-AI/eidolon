import { useState } from 'react'
import { Link } from 'react-router'

import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/components/ui/dialog'
import { SearchField } from '@/components/ui/search-field'
import { useI18n } from '@/i18n/context'

import { AgentContext } from './agent-context'
import { ConversationThread } from './runtime-conversations'
import type { OrganizationSnapshot } from './types'

export type WorkspaceSearchResult = { kind: 'agent' | 'objective' | 'conversation'; id: string; label: string }

/** Only the current loaded snapshot is indexed. No history fetch, chat open, or acknowledgement. */
export function searchWorkspace(snapshot: OrganizationSnapshot, query: string): WorkspaceSearchResult[] {
  const words = query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean)

  if (!words.length) return []

  const matches = (text: string) => words.every(word => text.toLocaleLowerCase().includes(word))

  return [
    ...snapshot.objectives.filter(item => matches(`${item.title} ${item.description} ${item.id}`))
      .map(item => ({ kind: 'objective' as const, id: item.id, label: item.title })),
    ...snapshot.agents.filter(item => matches(`${item.name} ${item.id} ${item.identityId ?? ''} ${item.role} ${item.team} ${item.purpose ?? ''}`))
      .map(item => ({ kind: 'agent' as const, id: item.id, label: item.name })),
    ...(snapshot.conversations ?? []).filter(item => matches(`${item.subject} ${item.id} ${item.participants.map(person => person.name).join(' ')}`))
      .map(item => ({ kind: 'conversation' as const, id: item.id, label: item.subject }))
  ]
}

export function WorkspaceSearch({ snapshot, profile, onClose, onReturnFocus, isCurrent }: {
  snapshot: OrganizationSnapshot
  profile: string
  onClose: () => void
  onReturnFocus: () => void
  isCurrent: () => boolean
}) {
  const { t } = useI18n()
  const copy = t.organizationHome.workspace
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState<WorkspaceSearchResult | null>(null)
  const results = searchWorkspace(snapshot, query)
  const agent = selected?.kind === 'agent' ? snapshot.agents.find(item => item.id === selected.id) : undefined
  const conversation = selected?.kind === 'conversation' ? snapshot.conversations?.find(item => item.id === selected.id) : undefined

  return <Dialog onOpenChange={open => { if (!open) onClose() }} open>
    <DialogContent className="eidolon eid-workspace-search" onCloseAutoFocus={event => { event.preventDefault(); onReturnFocus() }} onEscapeKeyDown={event => { event.preventDefault(); event.stopPropagation(); onClose() }}>
      <DialogTitle>{copy.search}</DialogTitle>
      <DialogDescription>{copy.searchNote(profile)}</DialogDescription>
      <SearchField aria-label={copy.search} onChange={value => { setQuery(value); setSelected(null) }} placeholder={copy.searchPlaceholder} value={query} />
      <div className="eid-workspace-search-results" onClick={event => { if ((event.target as HTMLElement).closest('a')) onClose() }} onClickCapture={event => {
        if (!isCurrent()) { event.preventDefault(); event.stopPropagation(); onClose() }
      }}>
        {selected ? <>
          <Button onClick={() => setSelected(null)} size="micro" variant="ghost">{copy.backToResults}</Button>
          {agent && <section aria-label={agent.name}><h2>{agent.name}</h2><p>{agent.role} · {agent.team}</p><AgentContext agent={agent} snapshot={snapshot} /></section>}
          {conversation && <ConversationThread conversation={conversation} snapshot={snapshot} />}
          {!agent && !conversation && <p role="status">{copy.noLongerLoaded}</p>}
        </> : <>
          {query.trim() && <p role="status">{copy.resultCount(results.length)}</p>}
          <ul>{results.slice(0, 30).map(item => <li key={`${item.kind}:${item.id}`}>
            {item.kind === 'objective' ? <Link aria-label={`${item.label} · ${copy.kinds[item.kind]} · ${item.id}`} onClick={onClose} to={`/objectives/${encodeURIComponent(item.id)}`}><span>{item.label}</span><small>{copy.kinds[item.kind]} · {item.id}</small></Link>
              : <Button aria-label={`${item.label} · ${copy.kinds[item.kind]} · ${item.id}`} onClick={() => setSelected(item)} variant="ghost"><span>{item.label}</span><small>{copy.kinds[item.kind]} · {item.id}</small></Button>}
          </li>)}</ul>
          {results.length > 30 && <p>{copy.refine}</p>}
        </>}
      </div>
    </DialogContent>
  </Dialog>
}
