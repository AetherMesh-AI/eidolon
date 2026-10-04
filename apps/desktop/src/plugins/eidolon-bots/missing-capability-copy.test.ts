import { host } from '@aethermesh/plugin-sdk'
import type { PluginContext } from '@aethermesh/plugin-sdk'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { notifyBotOpenFailure, prepareBotSource } from './canonical-chat'
import { newBotChat } from './data'
import { BOTS_LOCALES } from './i18n'
import { setPluginCtx } from './shared'
import type { RosterRow } from './types'

const bot = { name: 'alpha', sourceScoped: true, connectionId: 'fixture', connectionKind: 'remote' } as RosterRow
const originalNewChat = host.newChat
const originalRequestProfile = host.requestProfile

afterEach(() => {
  host.newChat = originalNewChat
  host.requestProfile = originalRequestProfile
  setPluginCtx(null)
  vi.restoreAllMocks()
})

describe('missing desktop capabilities give actionable product guidance without dispatch', () => {
  it('keeps translated and pre-registration fallback guidance aligned', async () => {
    const notify = vi.spyOn(host, 'notify').mockReturnValue('fixture')
    const notifyError = vi.spyOn(host, 'notifyError').mockReturnValue('fixture')
    host.newChat = undefined as never
    host.requestProfile = undefined as never

    for (const locale of [null, ...Object.keys(BOTS_LOCALES)]) {
      const bundle = BOTS_LOCALES[(locale ?? 'en') as keyof typeof BOTS_LOCALES] as { bot: Record<string, string> }
      setPluginCtx(locale ? { i18n: { t: (key: string) => bundle.bot[key.split('.')[1]] } } as unknown as PluginContext : null)
      newBotChat(bot)
      expect(notify).toHaveBeenLastCalledWith({ kind: 'error', message: bundle.bot.openAnotherChatUnsupported })
      expect(bundle.bot.openAnotherChatUnsupported).toContain('Eidolon')
      expect(bundle.bot.remoteConnectionsUnsupported).toContain('Eidolon')
      let failure: unknown

      try {
        await prepareBotSource(bot)
      } catch (error) {
        failure = error
      }

      expect(failure).toBeInstanceOf(Error)
      expect((failure as Error).message).toBe(bundle.bot.remoteConnectionsUnsupported)
      notifyBotOpenFailure(failure, bot, 'Could not reach fixture')
      expect(notifyError).toHaveBeenLastCalledWith(failure, 'Could not reach fixture')
    }
  })

  it('refuses an unscoped draft without invoking the available new-chat capability', () => {
    const newChat = vi.fn()
    host.newChat = newChat
    const notify = vi.spyOn(host, 'notify').mockReturnValue('fixture')
    newBotChat({ name: 'alpha' } as RosterRow)
    expect(newChat).not.toHaveBeenCalled()
    expect(notify).toHaveBeenCalledWith({ kind: 'error', message: 'Update Eidolon to open another Bot chat.' })
  })
})
