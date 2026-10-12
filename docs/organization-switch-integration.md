# Local organization switch integration

This review branch contains unchanged reviewed prerequisites PR92 (`2da498adb4b05f202213ec092142daf7f5cebef5`) and PR93 (`89b72ffe3bbf85639c9b66e78bef4d139aa0dea8`), plus the earlier preparation commit. The production diff begins after integration baseline `a25e22f010ce9bf10da66b8629404045573ccefc`. Neither prerequisite branch was rewritten or merged to main.

## Changes

`selectConnectionWithOutcome` distinguishes committed, unchanged and superseded requests. It reports the requested route, observed descriptor at settlement, reset attempt, observed publication and any dial/activation/bookkeeping/refresh error. The descriptor is not independent socket authentication evidence; page callers still check their current connected scope and owner identity. Shared foreground intent determines supersession even when the descriptor returns to the same route. The legacy Sessions wrapper retains rejection behavior. Refresh now has a finite timeout.

The existing background-work picker consumes that result and refuses obsolete navigation even when the old target is current again. Its organization switch operation coalesces identical current requests and enables observation before dialing. No new organization selector is enabled by this change.

Every shared source-switch intent, including profile and Settings doors, changes the organization read policy before route publication. Thereafter passive mounted and remounted organization refreshes use only `organization.observe` on the captured socket. They cannot reconnect through the legacy request helper or fall back to `organization.snapshot`. Shared-profile, malformed, mismatched, unconfigured and unsupported responses remain unavailable. Explicit execution mutations retain their existing authorization path. Before any switch intent, initial legacy loading is unchanged.

A scope check immediately before dispatch also revokes an already queued read. Observation epochs fence A→B→A even on a reused socket. Existing intake/chat/renewal stores are reused without introducing a second cache or changing their persistence.

## Deterministic coverage

The production-store regressions cover dial failure, duplicate pending requests, newer intent during reset, activation failure/nonlanding/timeout with independent reset recovery, post-publication timeout, refresh failure, and ABA through profile/activation doors. Additional tests exercise the real reset callback failure and organization-request coalescing. The rendered work picker rejects an explicit superseded result despite matching owner identity.

Mounted-provider tests exercise the real shared intent entry point, captured observation requests, ABA cancellation, remount, and unavailable scopes without fallback. Existing draft-reset regressions remain baseline preservation evidence, not proof of a full native switch journey.

## Remaining verification and release gates

This is a local review diff. No new publication, hosted CI, native Electron run or live paid provider call accompanies it. The integration tests use deterministic transports and do not establish live-model reliability. A real-store-to-native-shell journey, successful return with uncertain intake/send/renewal intent, keyboard behavior, and backend stopped-scheduler evidence are still required before enabling a new visible organization selector. The observation API's separately reviewed backend evidence does not substitute for a combined native integration run.

PR92's previously rejected merge remains blocked pending explicit approval resolution; no retry was attempted. PR93 remains draft under the parent hold. Local development does not resolve either merge gate. The earlier preparation document is historical and its proposed-contract wording should be read alongside this implemented contract.

## Independent review corrections

The initial integration missed mounted passive detail reads: `/requests` defaults to attention and called the service-backed legacy RPC even though its snapshot was observational. The follow-up routes every passive adapter read through a fixed, authenticated, non-mutating detail endpoint, with backend no-service/no-policy-write assertions and mounted attention coverage. The initial hosted run is evidence only for its original head, never the corrected source.

Publication metadata is now independent of foreground intent. Once this operation enters the serialized activation/reset, a later browse-only intent can supersede navigation without erasing the fact that activation published. A real-profile-store regression checks successful publication after reset and supersession; this corrects outcome metadata, not a demonstrated rollback/navigation bug.
