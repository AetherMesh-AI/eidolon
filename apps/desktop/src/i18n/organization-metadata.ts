/** Foundation copy uses the catalog fallback until translations are reviewed. */
export const organizationMetadataEn = {
  title: 'Organization and owner',
  unnamed: 'Unnamed organization',
  owner: 'Owner',
  edit: 'Edit organization and owner names',
  note: 'Names describe this organization and its human owner. They do not change permissions or start work.',
  organizationName: 'Organization name',
  ownerName: 'Your display name',
  save: 'Save names',
  cancel: 'Cancel',
  refresh: 'Refresh saved names',
  retry: 'Check original Save',
  saving: 'Saving names…',
  loading: 'Loading saved names…',
  unavailable: 'Names are unavailable on this connection. Connect to the organization’s own runtime to edit them.',
  unsupported: 'This runtime does not support editing organization names.',
  invalid: 'The runtime rejected these names. Refresh the saved version before trying again.',
  conflict: 'The saved organization changed. Refresh its names, review your draft, then save again.',
  uncertain:
    'The Save result is unknown. Your original request is retained for this organization; no new request will be sent automatically.',
  storage: 'The saved names could not be read. Refresh to try again.',
  blockedRetry:
    'The original organization is not available. Keep this request until its original connection and identity can be confirmed.',
  savedNames: (organization: string, owner: string) => `Saved names: ${organization} · ${owner}`,
  revision: (revision: number) => `Saved revision ${revision}`
}
