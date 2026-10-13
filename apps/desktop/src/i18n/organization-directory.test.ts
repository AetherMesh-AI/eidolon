import { expect, it } from 'vitest'

import { organizationDirectoryCopy } from './organization-directory'
import { directoryActivationCopy } from './organization-directory-activation'

it.each(['ja', 'zh', 'zh-hant', 'ar', 'ru'] as const)('supplies complete localized inspection and disabled-selection copy in %s', locale => {
  for (const key of Object.keys(organizationDirectoryCopy.en) as Array<keyof typeof organizationDirectoryCopy.en>) {
    expect(organizationDirectoryCopy[locale][key]?.trim()).not.toBe('')
    expect(organizationDirectoryCopy[locale][key]).not.toBe(organizationDirectoryCopy.en[key])
  }
})

it.each(['ja', 'zh', 'zh-hant', 'ar', 'ru'] as const)('localizes confirmation and every outcome in %s', locale => {
  for (const key of Object.keys(directoryActivationCopy.en) as Array<keyof typeof directoryActivationCopy.en>) {
    expect(directoryActivationCopy[locale][key].trim()).not.toBe('')
    expect(directoryActivationCopy[locale][key]).not.toBe(directoryActivationCopy.en[key])
  }
})
