import { createHash } from 'node:crypto'

export interface OrganizationEvidenceContext {
  evidence?: Array<{
    id: string
    sha256: string
    content: string | { bodySha256: string; utf8Bytes: number }
  }>
  evidenceBodies?: Record<string, string>
}

/** Resolve and hash-check complete bodies before any scripted model approves. */
export function exactOrganizationEvidence(context: OrganizationEvidenceContext) {
  return (context.evidence ?? []).map(artifact => {
    const reference = artifact.content
    const content = typeof reference === 'string' ? reference : context.evidenceBodies?.[reference.bodySha256]

    if (
      typeof content !== 'string' ||
      !content.length ||
      createHash('sha256').update(content).digest('hex') !== artifact.sha256
    ) {
      throw new Error(`The model did not receive the complete exact evidence body: ${artifact.id}`)
    }

    if (
      typeof reference !== 'string' &&
      (reference.bodySha256 !== artifact.sha256 || reference.utf8Bytes !== Buffer.byteLength(content, 'utf8'))
    ) {
      throw new Error(`Evidence body reference differs from its exact retained bytes: ${artifact.id}`)
    }

    return { id: artifact.id, sha256: artifact.sha256, content }
  })
}
