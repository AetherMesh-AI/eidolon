/** One bounded loopback model script. No provider login or external network. */
import { randomUUID } from 'node:crypto'
import http from 'node:http'

import type { MockServer } from './mock-server'

export async function startHandoffNativeModel(rendezvous: string) {
  const requests: string[] = []
  const toolResults: Array<{ success: boolean; result?: Record<string, unknown>; reason?: string }> = []
  let publicId = ''
  let release: (() => void) | undefined
  let heldResolve!: () => void
  let closed = false
  const held = new Promise<void>(resolve => { heldResolve = resolve })

  const server = http.createServer(async (request, response) => {
    const reply = (value: unknown, code = 200) => {
      response.writeHead(code, { 'Content-Type': 'application/json' })
      response.end(JSON.stringify(value))
    }

    if (request.url === `/${rendezvous}/v1/models` && request.method === 'GET') {
      reply({ data: [{ id: 'mock-model', object: 'model', created: 1, owned_by: 'synthetic-fixture' }] })

      return
    }

    if (request.url !== `/${rendezvous}/v1/chat/completions` || request.method !== 'POST' || closed) {
      reply({ error: 'Unexpected synthetic endpoint' }, 404)

      return
    }

    let bytes = 0
    const chunks: Buffer[] = []

    try {
      for await (const chunk of request) {
        const buffer = Buffer.from(chunk)
        bytes += buffer.length

        if (bytes > 1024 * 1024) {
          throw new Error('Synthetic request too large')
        }

        chunks.push(buffer)
      }

      const raw = Buffer.concat(chunks).toString('utf8')
      const body = JSON.parse(raw) as { messages: Array<{ role: string; content: string }> }
      requests.push(raw)
      const step = requests.length

      if (step === 1) {
        heldResolve()
        await new Promise<void>((resolve, reject) => {
          const timer = setTimeout(() => reject(new Error('Synthetic owner rendezvous expired')), 120_000)

          release = () => { clearTimeout(timer); resolve() }
        })
      } else {
        const message = [...body.messages].reverse().find(item => item.role === 'tool')

        if (!message) {
          throw new Error('Missing synthetic tool result')
        }

        toolResults.push(JSON.parse(message.content))
      }

      if (closed || step > 5) {
        throw new Error('Synthetic model lifecycle ended')
      }

      if (step === 2) {
        const handoffs = toolResults[0].result?.replacementHandoffs as Array<{ handoffId: string }> | undefined
        publicId = handoffs?.[0]?.handoffId ?? ''

        if (!publicId || handoffs?.length !== 1) {
          throw new Error('Expected one exact prepared handoff')
        }
      }

      const name = step === 1 ? 'organization_credential_catalog' : 'organization_credential_replace'
      const args = step === 1 ? {} : { handoffId: step === 2 ? randomUUID() : publicId }

      const message = step < 5
        ? { role: 'assistant', content: null, tool_calls: [{ id: `synthetic-handoff-${step}`, type: 'function',
            function: { name, arguments: JSON.stringify(args) } }] }
        : { role: 'assistant', content: JSON.stringify({ summary: 'Completed synthetic local replacement',
            deliverable: `Non-bearer authorization reference ${publicId}` }) }

      reply({ id: `synthetic-handoff-${step}`, object: 'chat.completion', created: 1, model: 'mock-model',
        choices: [{ index: 0, message, finish_reason: step < 5 ? 'tool_calls' : 'stop' }],
        usage: { prompt_tokens: 30, completion_tokens: 20, total_tokens: 50 } })
    } catch {
      reply({ error: 'Synthetic handoff model failed' }, 400)
    }
  })

  await new Promise<void>(resolve => { server.listen(0, '127.0.0.1', resolve) })
  const address = server.address()

  if (!address || typeof address === 'string') {
    throw new Error('Missing synthetic model listener')
  }

  const waitForHeld = async () => {
    let timer: ReturnType<typeof setTimeout> | undefined

    try {
      await Promise.race([held, new Promise<never>((_, reject) => {
        timer = setTimeout(() => reject(new Error('Synthetic worker did not reach model')), 30_000)
      })])
    } finally {
      clearTimeout(timer)
    }
  }

  const mock: MockServer = {
    port: address.port, url: `http://127.0.0.1:${address.port}/${rendezvous}`, receivedPrompts: [],
    waitForHeldStream: waitForHeld, waitForHeldCompletion: waitForHeld,
    releaseHeldStream: () => { release?.() }, heldCompletionCount: () => requests.length,
    close: async () => {
      closed = true
      release?.()
      server.closeAllConnections()
      await new Promise<void>((resolve, reject) => { server.close(error => error ? reject(error) : resolve()) })
    }
  }

  return { mock, requests, toolResults }
}
