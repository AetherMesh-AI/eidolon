import type { PreparedValidationScope } from './runtime-prepared-validation-types'
import { organizationMetadataClient, readOrganizationMetadataScope } from './use-organization-metadata'

/** Both scopes synchronously capture the same owner socket; no reconnect/retarget helper. */
export function readPreparedValidationScope(): PreparedValidationScope {
  const scope = readOrganizationMetadataScope()
  const preparationScope = readOrganizationMetadataScope(180000)
  const metadata = organizationMetadataClient.getSnapshot().descriptor?.metadata

  return { ...scope, organizationId: metadata?.organizationId ?? null, ownerId: metadata?.owner.ownerId ?? null,
    request: <T>(method: string, params: Record<string, unknown>) =>
      (method === 'organization.previewPreparedValidation' || method === 'organization.savePreparedValidation'
        ? preparationScope : scope).request<T>(method, params) }
}
