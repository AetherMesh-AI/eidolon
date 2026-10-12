import { expect, it } from 'vitest'

import { organizationDirectoryCopy } from './organization-directory'

it.each(['ja', 'zh', 'zh-hant', 'ar', 'ru'] as const)('supplies complete localized inspection and disabled-selection copy in %s', locale => {
  for (const key of Object.keys(organizationDirectoryCopy.en) as Array<keyof typeof organizationDirectoryCopy.en>) {
    expect(organizationDirectoryCopy[locale][key]?.trim()).not.toBe('')
    expect(organizationDirectoryCopy[locale][key]).not.toBe(organizationDirectoryCopy.en[key])
  }
})
