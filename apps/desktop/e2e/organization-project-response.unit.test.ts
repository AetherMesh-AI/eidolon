import { expect, it } from 'vitest'

import { projectProviderResponse } from './organization-project-response'

it('responds to omitted and explicit false stream flags without changing the exact decision', () => {
  const message = { role: 'assistant', content: '{"workers":1,"tasks":[]}' }
  const implicit = projectProviderResponse(message, 1)
  expect(projectProviderResponse(message, 1, false)).toEqual(implicit)
  expect(implicit.choices[0]).toEqual({ index: 0, message, finish_reason: 'stop' })
  expect(implicit.usage.total_tokens).toBe(implicit.usage.prompt_tokens + implicit.usage.completion_tokens)
  expect(() => projectProviderResponse(message, 1, true)).toThrow('non-streaming completion')
})

it('retains exact read-only tool requests and their continuation reason', () => {
  const message = {
    role: 'assistant',
    content: null,
    tool_calls: [
      { id: 'read-base', type: 'function', function: { name: 'read_file', arguments: '{"path":"root0/app.py"}' } }
    ]
  }
  expect(projectProviderResponse(message, 2).choices[0]).toEqual({ index: 0, message, finish_reason: 'tool_calls' })
})
