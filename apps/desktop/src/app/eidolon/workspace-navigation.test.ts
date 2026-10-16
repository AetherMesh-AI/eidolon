import { expect, it } from 'vitest'

import { objectiveHref, objectiveReturnTarget, workspaceReturnTarget } from './workspace-navigation'

it.each(['https://evil.test', '//evil.test', '/\\evil.test', '/settings', '/messages#fragment', '/messages\n', '/objectives/other', '/work/../settings'])('rejects an unsafe or unrelated return target %s', target => {
  expect(workspaceReturnTarget(target)).toBeNull()
})

it('encodes identifiers and binds return and focused request to the owner', () => {
  const href = objectiveHref('id /?#', '/messages?member=worker&owner=one&identity=identity', 'one', 'request &')
  const url = new URL(href, 'https://workspace.invalid')
  expect(url.pathname).toBe('/objectives/id%20%2F%3F%23')
  expect(url.searchParams.get('request')).toBe('request &')
  expect(url.searchParams.get('requestOwner')).toBe('one')
  expect(objectiveReturnTarget(url.searchParams, 'one')).toBe('/messages?member=worker&owner=one&identity=identity')
  expect(objectiveReturnTarget(url.searchParams, 'two')).toBe('/objectives')
  expect(objectiveReturnTarget(url.searchParams)).toBe('/objectives')
})

it('keeps canonical links safe when no owner is known', () => {
  expect(objectiveHref('one', '/home', undefined, 'request')).toBe('/objectives/one')
  expect(objectiveReturnTarget(new URLSearchParams('from=https://evil.test&originOwner=one'), 'one')).toBe('/objectives')
})
