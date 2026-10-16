import { randomUUID } from 'node:crypto'

import { expect, it } from 'vitest'

import { startHandoffNativeModel } from './organization-credential-handoff-native-model'

it('binds the loopback rendezvous and scripts invalid reference, replacement and idempotent replay', async () => {
  const model = await startHandoffNativeModel(randomUUID())

  const send = (content?: unknown) => fetch(`${model.mock.url}/v1/chat/completions`, {
    method: 'POST', body: JSON.stringify({ messages: content ? [{ role: 'tool', content: JSON.stringify(content) }] : [] })
  }).then(response => response.json())

  try {
    const wrong = await fetch(`http://127.0.0.1:${model.mock.port}/wrong/v1/models`)
    expect(wrong.status).toBe(404)
    const pending = send()
    await model.mock.waitForHeldCompletion()
    expect(model.requests).toHaveLength(1)
    model.mock.releaseHeldStream()
    const catalog = await pending
    expect(catalog.choices[0].message.tool_calls[0].function.name).toBe('organization_credential_catalog')
    const handoffId = randomUUID()
    const invalid = await send({ success: true, result: { replacementHandoffs: [{ handoffId }] } })
    expect(JSON.parse(invalid.choices[0].message.tool_calls[0].function.arguments).handoffId).not.toBe(handoffId)
    const replacement = await send({ success: false, reason: 'handoff_unavailable' })
    expect(JSON.parse(replacement.choices[0].message.tool_calls[0].function.arguments)).toEqual({ handoffId })
    const receipt = { success: true, result: { action: 'replace_value', entryId: 'synthetic-entry', revision: 2, version: 1 } }
    const replay = await send(receipt)
    expect(JSON.parse(replay.choices[0].message.tool_calls[0].function.arguments)).toEqual({ handoffId })
    const final = await send(receipt)
    expect(JSON.parse(final.choices[0].message.content).deliverable).toContain(handoffId)
    expect(model.toolResults[2]).toEqual(model.toolResults[3])
    expect(model.requests).toHaveLength(5)
    const extra = await fetch(`${model.mock.url}/v1/chat/completions`, { method: 'POST', body: '{}' })
    expect(extra.status).toBe(400)
  } finally {
    await model.mock.close()
  }
})

it('aborts a held response on cleanup rather than releasing a new tool request', async () => {
  const model = await startHandoffNativeModel(randomUUID())

  const pending = fetch(`${model.mock.url}/v1/chat/completions`, {
    method: 'POST', body: JSON.stringify({ messages: [] })
  }).then(response => response.status, () => 'aborted')

  await model.mock.waitForHeldCompletion()
  await model.mock.close()
  expect(['aborted', 400]).toContain(await pending)
  expect(model.requests).toHaveLength(1)
  expect(model.toolResults).toEqual([])
})
