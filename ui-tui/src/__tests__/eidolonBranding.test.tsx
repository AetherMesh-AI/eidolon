import { PassThrough } from 'stream'

import { renderSync } from '@aethermesh/ink'
import React from 'react'
import { describe, expect, it } from 'vitest'

import { Banner, SessionPanel } from '../components/branding.js'
import { stripAnsi } from '../lib/text.js'
import { DEFAULT_THEME } from '../theme.js'

describe('product and provider branding', () => {
  it.each([48, 120])('keeps the selected model distinct from app branding at %s columns', columns => {
    const stdout = new PassThrough()
    const stdin = new PassThrough()
    const stderr = new PassThrough()
    let output = ''

    Object.assign(stdout, { columns, isTTY: false, rows: 60 })
    Object.assign(stdin, { isTTY: false })
    Object.assign(stderr, { isTTY: false })
    stdout.on('data', chunk => {
      output += chunk.toString()
    })

    const model = 'NousResearch/Hermes-4.5'

    const instance = renderSync(
      <>
        <Banner maxWidth={columns} t={DEFAULT_THEME} />
        <SessionPanel info={{ model, skills: {}, tools: {} }} maxWidth={columns} sid="test" t={DEFAULT_THEME} />
      </>,
      {
        patchConsole: false,
        stderr: stderr as NodeJS.WriteStream,
        stdin: stdin as NodeJS.ReadStream,
        stdout: stdout as NodeJS.WriteStream
      }
    )

    try {
      const text = stripAnsi(output)

      expect(text).toContain(DEFAULT_THEME.brand.name)
      expect(text).toContain('AetherMesh')
      expect(text).toContain(model.split('/').pop())
      expect(text).not.toContain('Nous Research')
    } finally {
      instance.unmount()
      instance.cleanup()
    }
  })
})
