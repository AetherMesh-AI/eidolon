import type { ReactNode } from 'react'

import { Button } from '@/components/ui/button'
import { useI18n } from '@/i18n'
import { Bell, Cpu, KeyRound, Lock, Palette, Wrench } from '@/lib/icons'

import type { SettingsView } from './types'

/** Compose existing owners; never aggregate their writes into a global save. */
export function SettingsOverview({ editor, notifications, onSelect, editorOnly = false, editorLabel, summaries }: {
  summaries?: Partial<Record<SettingsView, ReactNode>>
  editorLabel?: string
  editorOnly?: boolean
  editor: ReactNode
  notifications: ReactNode
  onSelect(view: SettingsView): void
}) {
  const { t } = useI18n()
  const copy = t.settingsWorkspace

  const cards = [
    { id: 'config:model', title: t.settings.sections.model, icon: Cpu, content: editor },
    { id: 'notifications', title: t.settings.nav.notifications, icon: Bell, content: notifications },
    { id: 'providers', title: t.settings.nav.providers, icon: KeyRound },
    { id: 'config:safety', title: t.settings.sections.safety, icon: Lock },
    { id: 'config:workspace', title: t.settings.sections.workspace, icon: Wrench },
    { id: 'config:appearance', title: t.settings.sections.appearance, icon: Palette }
  ] as const

  return <div className={editorOnly ? 'eid-settings-cards eid-settings-model-only' : 'eid-settings-cards'}>
    {cards.filter(card => !editorOnly || card.id === 'config:model').map(card => <section aria-label={editorOnly ? editorLabel ?? card.title : card.title} className={'content' in card ? 'eid-settings-card eid-settings-card-editor' : 'eid-settings-card'} key={card.id}>
      <header>
        <span className="eid-settings-card-icon"><card.icon /></span>
        <div><h3>{card.title}</h3><p>{copy.sections[card.id]}</p></div>
        <Button aria-label={copy.openSection.replace('{section}', card.title)} onClick={() => onSelect(card.id)} size="sm" type="button" variant="secondary">{copy.openSection.replace('{section}', card.title)}</Button>
      </header>
      {'content' in card ? <div className="eid-settings-card-body">{card.content}</div> : summaries?.[card.id] && <div className="eid-settings-card-summary">{summaries[card.id]}</div>}
    </section>)}
  </div>
}
