/** Real profile ledger + loopback scripted provider. No live model access. */
import assert from 'node:assert/strict'
import http from 'node:http'

import {
  buildAppEnv,
  createSandbox,
  launchDesktop,
  type MockBackendFixture,
  waitForAppReady,
  writeEnvFile,
  writeMockProviderConfig
} from './fixtures'
import { startMockServer } from './mock-server'
import { organizationProviderTarget } from './organization-provider-target'

export const firstMessage = 'Owner chat fixture: please explain your role using only this conversation.'
export const continuedMessage = 'Owner chat fixture: continue after explicit allowance renewal.'
export const continuedReply = 'The same conversation continues with the renewed finite allowance.'
export const secondMessage = 'Owner chat fixture: hold this second reply while I cancel.'
export const firstReply =
  'I can discuss my role here. Please use Create objective for work and formal controls for permissions.'
export const lateReply = 'This cancelled reply must never enter the conversation.'

interface ProviderPayload {
  messages?: Array<{ role: string; content: unknown }>
  tools?: unknown[]
  stream?: boolean
  max_tokens?: number
  max_completion_tokens?: number
}

export async function setupOwnerChatFixture() {
  const calls: ProviderPayload[] = []
  const providerErrors: string[] = []
  const pending: Array<() => void> = []
  const mock = await startMockServer()
  const sandbox = createSandbox('owner-chat')
  let fixture: MockBackendFixture | undefined

  const server = http.createServer((request, response) => {
    const chunks: Buffer[] = []
    request.on('data', chunk => chunks.push(Buffer.from(chunk)))
    request.on('end', () => {
      const body = Buffer.concat(chunks)

      try {
        const target = organizationProviderTarget(mock.url, request.method, request.url)
        const payload = body.length ? (JSON.parse(body.toString('utf8')) as ProviderPayload) : null
        const contents = JSON.stringify(payload?.messages ?? [])

        if (contents.includes('EIDOLON_OWNER_CHAT_V1')) {
          assert.equal(payload?.tools?.length ?? 0, 0, 'Owner chat must be tool-free on the actual HTTP request')
          const outputLimit = payload?.max_completion_tokens ?? payload?.max_tokens
          assert.ok(typeof outputLimit === 'number' && outputLimit > 0 && outputLimit <= 2048)
          const ownerPrompt = payload?.messages?.find(message => message.role === 'user')?.content
          assert.equal(typeof ownerPrompt, 'string')
          const marker = 'Owner conversation, exact reply target:\n'
          assert.ok((ownerPrompt as string).startsWith(marker))

          const submitted = JSON.parse((ownerPrompt as string).slice(marker.length)) as {
            identity: { id: string; identityId: string }
            conversation: { identityId: string; messages: Array<{ text: string }> }
          }

          assert.deepEqual(Object.keys(submitted).sort(), ['conversation', 'identity'])
          assert.equal(submitted.identity.id, 'worker-1')
          assert.equal(submitted.conversation.identityId, submitted.identity.identityId)
          assert.equal(submitted.conversation.messages[0].text, firstMessage)
          calls.push(payload!)
          assert.ok(calls.length <= 3, 'No automatic provider retry or duplicate replay')
          const latestText = submitted.conversation.messages.at(-1)?.text
          const content = JSON.stringify({
            reply:
              latestText === secondMessage ? lateReply : latestText === continuedMessage ? continuedReply : firstReply
          })
          const identity = { id: `owner-chat-${calls.length}`, created: 1, model: 'mock-model' }
          const usage = { prompt_tokens: 40, completion_tokens: 40, total_tokens: 80 }

          const reply = () => {
            if (response.destroyed) {
              return
            }

            if (payload?.stream) {
              response.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' })
              response.write(
                `data: ${JSON.stringify({ ...identity, object: 'chat.completion.chunk', choices: [{ index: 0, delta: { role: 'assistant', content }, finish_reason: null }] })}\n\n`
              )
              response.write(
                `data: ${JSON.stringify({ ...identity, object: 'chat.completion.chunk', choices: [{ index: 0, delta: {}, finish_reason: 'stop' }], usage })}\n\n`
              )
              response.end('data: [DONE]\n\n')
            } else {
              response.writeHead(200, { 'Content-Type': 'application/json' })
              response.end(
                JSON.stringify({
                  ...identity,
                  object: 'chat.completion',
                  choices: [{ index: 0, message: { role: 'assistant', content }, finish_reason: 'stop' }],
                  usage
                })
              )
            }
          }

          if (latestText === secondMessage) {
            pending.push(reply)
          } else {
            reply()
          }

          return
        }

        const upstream = http.request(
          target,
          { method: request.method, headers: { ...request.headers, host: new URL(mock.url).host } },
          result => {
            response.writeHead(result.statusCode ?? 500, result.headers)
            result.pipe(response)
          }
        )

        upstream.on('error', error => {
          providerErrors.push(error.message)

          if (!response.headersSent) {
            response.writeHead(502)
          }

          response.end()
        })
        upstream.end(body)
      } catch (error) {
        providerErrors.push(error instanceof Error ? error.message : String(error))
        response.writeHead(500, { 'Content-Type': 'application/json' })
        response.end(JSON.stringify({ error: { message: 'Owner chat fixture invariant failed' } }))
      }
    })
  })

  const releaseReplies = () => pending.splice(0).forEach(reply => reply())

  const closeProvider = () =>
    new Promise<void>((resolve, reject) => server.close(error => (error ? reject(error) : resolve())))

  const cleanup = async () => {
    releaseReplies()

    try {
      await fixture?.app.close()
    } finally {
      try {
        if (server.listening) {
          await closeProvider()
        }
      } finally {
        try {
          await mock.close()
        } finally {
          sandbox.cleanup()
        }
      }
    }
  }

  try {
    await new Promise<void>((resolve, reject) => {
      server.once('error', reject)
      server.listen(0, '127.0.0.1', resolve)
    })
    const address = server.address()

    if (!address || typeof address === 'string') {
      throw new Error('Owner chat provider did not bind loopback TCP')
    }

    const providerUrl = `http://127.0.0.1:${address.port}`
    writeMockProviderConfig(
      sandbox.hermesHome,
      providerUrl,
      undefined,
      'approvals:\n  mode: manual\ncontext:\n  memory_trim:\n    enabled: false',
      128000,
      false
    )
    writeEnvFile(sandbox.hermesHome)
    const launch = await launchDesktop(buildAppEnv(sandbox))
    fixture = { ...launch, mock, mockUrl: providerUrl, sandbox, cleanup }
    await waitForAppReady(fixture, 120_000)
  } catch (error) {
    await cleanup()
    throw error
  }

  return { fixture, calls, providerErrors, releaseReplies }
}
