/** Credentials and query tokens never participate in renderer-visible identity. */
export function organizationEndpointIdentity(value?: string) {
  if (!value) { return null }

  try {
    const url = new URL(value)

    return `${url.origin}${url.pathname}`
  } catch { return null }
}

export function organizationOwnerKey(
  connectionId: string | null,
  profile: string,
  connection: { mode?: string; baseUrl?: string } | null
) {
  const endpoint = connection?.mode === 'remote' ? organizationEndpointIdentity(connection.baseUrl) : null

  return JSON.stringify([connectionId ?? (connection?.mode === 'remote' ? endpoint : 'local'), profile, endpoint])
}
