import { atom } from 'nanostores'

/** Once organization-owned selection begins, passive reads stay non-starting for
 * this window. Returning to a source or remounting the provider must not restart
 * its queued work. Explicit mutations retain their own authorization path. */
export const $organizationObservationEpoch = atom(0)
export const $organizationObservationSwitching = atom(false)
