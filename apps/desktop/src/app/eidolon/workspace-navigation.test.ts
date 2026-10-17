import { expect, it } from 'vitest'

import { contextualObjectiveHref, objectiveHref, objectiveReturnTarget, workspaceReturnTarget } from './workspace-navigation'

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

it.each(['/activity?activityOwner=one&activityEvent=record', '/organization?rosterOwner=one&rosterMember=agent&rosterIdentity=identity'])('allows only same-owner contextual return to %s', origin => {
  const href = objectiveHref('goal', origin, 'one')
  const params = new URL(href, 'https://workspace.invalid').searchParams
  expect(objectiveReturnTarget(params, 'one')).toBe(origin)
  expect(objectiveReturnTarget(params, 'two')).toBe('/objectives')
})

it('preserves an embedded objective self-link without nesting or losing its original return', () => {
  const search = '?from=%2Factivity%3FactivityOwner%3Done&originOwner=one'
  expect(contextualObjectiveHref('goal /', '/objectives/goal%20%2F', search, 'one')).toBe('/objectives/goal%20%2F' + search)
  expect(contextualObjectiveHref('other', '/objectives/goal', search, 'one')).toBe('/objectives/other')
})
