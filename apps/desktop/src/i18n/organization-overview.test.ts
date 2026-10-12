import { expect, it } from 'vitest'

import { TRANSLATIONS } from './catalog'

it.each(['ja', 'zh', 'zh-hant', 'ar', 'ru'] as const)('supplies localized home, health and identity controls while preserving stored names and formatted values (%s)', locale => {
  const copy = TRANSLATIONS[locale]
  const english = TRANSLATIONS.en

  for (const key of ['home', 'messages', 'work', 'heading', 'introduction', 'happening', 'needsYou', 'ready', 'newObjective', 'owner', 'outcomesUnavailable', 'requestsUnavailable', 'messagesNote', 'workNote', 'noAgents', 'chooseAgent', 'unknownStatus'] as const) {
    expect(copy.organizationHome[key]).not.toBe(english.organizationHome[key])
  }

  for (const key of ['title', 'unnamed', 'owner', 'edit', 'save', 'uncertain', 'blockedRetry'] as const) {
    expect(copy.organizationMetadata[key]).not.toBe(english.organizationMetadata[key])
  }

  expect(copy.organizationHome.health.heading).not.toBe(english.organizationHome.health.heading)

  for (const state of Object.keys(english.organizationHome.health.states) as Array<keyof typeof english.organizationHome.health.states>) {
    expect(copy.organizationHome.health.states[state]).not.toBe(english.organizationHome.health.states[state])
  }

  const owner = 'مالك / Avery 佐藤'
  const organization = 'Research & 開発'
  expect(copy.organizationMetadata.savedNames(organization, owner)).toContain(organization)
  expect(copy.organizationMetadata.savedNames(organization, owner)).toContain(owner)
  expect(copy.organizationHome.completedTasks('٦٠٪')).toContain('٦٠٪')
  expect(copy.organizationHome.health.activity('١٢', '٣٤')).toContain('١٢')
  expect(copy.organizationHome.health.activity('١٢', '٣٤')).toContain('٣٤')
})
