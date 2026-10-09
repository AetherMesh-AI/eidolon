# Owner conversations with organization members

The owner can open a private text conversation with any retained organization
member, including a Worker. This is independent of the reporting hierarchy.
The member's immutable identity owns the conversation within the selected
profile; a rename, navigation, or restart does not create a replacement chat.
An inactive or retired member's history remains readable but cannot accept new
messages. A replacement member never inherits the conversation implicitly.

## Discussion and authority

Text chat is discussion only. It cannot run tools, read files, create objectives,
resolve formal questions or permissions, transfer work, or mark work complete.
Use the existing explicit organization controls for those actions. In particular,
“yes” in a conversation is not a permission grant or acceptance receipt, and an
agent's reply is not reviewed evidence.

A turn receives only the member's public identity and its own owner conversation.
It does not receive another member's memory, internal conversations, project
files, objective contents, or unrelated transcripts. No attachment or filesystem
lookup is performed by this first version.

## Provider and budget boundaries

Each explicit send admits at most one bounded, tool-free call using the member's
configured provider/model (or its existing profile configuration). Existing
organization provider restrictions still apply. A provider transport whose
no-tools boundary cannot be established stays unavailable; there is no fallback
to unrestricted normal chat or a different paid provider. Opening and reading a
conversation does not start inference. Provider use may incur configured-provider
charges; the visible call/token limits are bounds, not a promise of free service.
Named provider identities are checked against their exact configured route even
when the transport adapter is named `custom`. The selected identity and actual
adapter/model are recorded separately; endpoint and credential details are not
added to the conversation or its audit view.

The conversation has its own durable admission budget, separate from objective
budgets. This initial version allows 32 explicit turns per immutable member
identity, reserves at most 32,768 input plus 2,048 output tokens per turn, and
stops a turn after 90 seconds. Messages and replies are limited to 6,000
characters. Context that exceeds the input bound is rejected instead of
silently dropping earlier conversation. The lifetime reservation ceiling is
1,114,112 tokens; actual reported usage is recorded separately when available. Calls and conservative token reservations survive errors, cancellation,
and restarts. Cancellation prevents a late reply from being accepted but cannot
promise the provider did no work or incurred no charge. Uncertain turns are never
automatically replayed. Retrying delivery with the same idempotency key retrieves
the same accepted turn instead of generating another call.

## Routing and voice

Owner-initiated chat is available at every rank. The desired unsolicited-contact
policy is Worker → assigned Manager → owning Executive → owner, with the
Executive normally consolidating routine updates. That routing enhancement is
separate from this text-chat slice. Existing urgent, denied, unclaimed, and
unresolved interventions remain visible in owner attention; no communication is
hidden to imitate the future routing policy.

The Call control is deliberately disabled and explains that voice is not yet
available. This change requests no microphone access, provisions no voice
provider, and performs no calls. Local-first voice can later reuse this exact
conversation once consent, cancellation, provider choice, and usage boundaries
are implemented and independently verified.

## Verification

Behavioral coverage includes exact identity/profile routing, duplicate and
concurrent admission, stale reply rejection, retired-member history, cancellation
and late replies, restart recovery, finite budget exhaustion, no tool execution,
and no formal authorization mutation. The native Electron fixture uses the real
backend and a loopback scripted provider; it does not call a live model service.

## Next increments

This is a bounded first chat slice, not the complete persistent-chat lifecycle.
The initial allowance is visible and cannot currently be replenished in the UI.
Before increasing it, add an explicit owner-configured chat budget or deliberate
replenishment action that preserves all previous usage receipts, never resets on
restart/rename, and shows the additional provider exposure before confirmation.
Increasing allowance must accompany bounded, cache-aware context management;
retain full history and exact reply provenance even when only an explicitly
identified conversation summary and recent turns fit in a prompt. Never obtain
continuation by replacing the member identity or silently starting another thread.

Management-first unsolicited routing and local-first voice follow that text
foundation. Neither is claimed as complete by this change.
