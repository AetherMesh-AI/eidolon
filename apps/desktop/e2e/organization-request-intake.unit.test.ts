import { expect, it } from 'vitest'

import { MOCK_REPLY, startMockServer } from './mock-server'

it('isolates the explicit intake fixture to its marked system request', async () => {
  const seen: string[] = []

  const server = await startMockServer({ requestIntakeReply: prompt => { seen.push(prompt);

 return '{"synthetic":true}' } })

  try {
    const send = async (system: string) => {
      const response = await fetch(`${server.url}/v1/chat/completions`, { method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model: 'mock-model', stream: false, messages: [
          { role: 'system', content: system }, { role: 'user', content: '{"organization":{"snapshotId":"fixture"}}' }
        ] }) })

      return response.json()
    }

    const ordinary = await send('Ordinary chat')

    expect(ordinary.choices[0].message.content).toBe(MOCK_REPLY)
    expect(seen).toEqual([])
    const intake = await send('EIDOLON_REQUEST_INTAKE_V1')

    expect(intake.choices[0].message.content).toBe('{"synthetic":true}')
    expect(intake.usage).toEqual({ prompt_tokens: 10, completion_tokens: 20, total_tokens: 30 })
    expect(seen).toHaveLength(1)
  } finally {
    await server.close()
  }
})
