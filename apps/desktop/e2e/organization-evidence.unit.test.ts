import { createHash } from 'node:crypto'

import { describe, expect, it } from 'vitest'

import { exactOrganizationEvidence } from './organization-evidence'

describe('scripted organization provider evidence', () => {
  const content = 'Complete retained evidence, including non-ASCII bytes: café 日本語.'
  const sha256 = createHash('sha256').update(content).digest('hex')
  const reference = { bodySha256: sha256, utf8Bytes: Buffer.byteLength(content, 'utf8') }
  const artifact = { id: 'task-evidence', sha256, content: reference }

  it('reads every full artifact through a shared lossless body while preserving evidence identity', () => {
    const evidence = [artifact, { ...artifact, id: 'integrated-evidence' }]
    expect(exactOrganizationEvidence({ evidence, evidenceBodies: { [sha256]: content } })).toEqual(
      evidence.map(item => ({ id: item.id, sha256, content }))
    )
  })

  it('refuses missing, changed, falsely identified or partially described body evidence', () => {
    const evidenceBodies = { [sha256]: content }
    const changed = (fields: Partial<typeof artifact>) => ({ evidence: [{ ...artifact, ...fields }], evidenceBodies })

    const invalid = [
      { evidence: [artifact], evidenceBodies: {} },
      { evidence: [artifact], evidenceBodies: { [sha256]: content.slice(1) } },
      changed({ sha256: '0'.repeat(64) }),
      changed({ content: { ...reference, bodySha256: '0'.repeat(64) } }),
      changed({ content: { ...reference, utf8Bytes: content.length } })
    ]

    for (const context of invalid) {
      expect(() => exactOrganizationEvidence(context)).toThrow(/evidence body|retained bytes/)
    }
  })
})
