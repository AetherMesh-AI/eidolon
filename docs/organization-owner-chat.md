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

The conversation has its own durable admission allowance, separate from objective
budgets. It starts with 32 explicit sends per immutable member identity. Each
send reserves at most 32,768 input plus 2,048 output tokens and stops after 90
seconds. Messages and replies are limited to 6,000 characters. Actual provider
usage is recorded separately from conservative reservations when available.
Cancellation prevents a late reply from being accepted but cannot promise that
the provider did no work or incurred no charge. Calls and token reservations
survive errors, cancellation, restarts, and allowance renewal; they are never
refunded. Uncertain turns are never automatically replayed. Retrying delivery
with the same idempotency key retrieves the same accepted turn.

### Deliberate allowance renewal

The owner can review and confirm an additional allowance for this exact member's
existing conversation. A renewal authorizes a finite number of future explicit
sends; it does not send a message or start inference. Each renewal adds 1–32
calls and their conservative token ceiling, with at most 32 unconsumed calls
available at once. The cumulative technical ceiling is 1,000,000 calls per
identity. This is a hard safety ceiling, not an automatic grant or a recommended
budget. There is no timer, automatic renewal, identity replacement, or silent
reset when the allowance runs out.

The confirmation displays the additional sends and token exposure. These are
admission bounds, not currency prices: actual configured-provider charges may
apply. The authenticated owner RPC binds the request to the exact thread,
identity, allowance version and organization policy generation. Its idempotency
key identifies an immutable renewal receipt, so repeated delivery cannot add the
allowance twice. A stale review must be refreshed; inactive or retired members
cannot receive renewals. Previous usage and reservations remain intact.

### Full history and bounded model context

The full transcript remains in the identity-owned ledger. Reading it uses bounded
pages (50 messages by default, at most 100), with an exact same-thread message
cursor. Loading earlier messages never changes the latest reply target. The
owner can continue reading earlier history without a provider call.

Each admitted send deterministically selects at most 100 recent messages, then
keeps the largest whole-turn suffix that fits the conservative input bound. It does not split an owner/reply pair or
omit the exact message being replied to. If even the minimum required context
cannot fit, admission fails before reserving a call. The turn records exact
included message IDs and the omitted-message count; the UI makes clear that older
messages are outside model context but remain in history. The executor uses the
recorded selection rather than silently rebuilding a different context.

There is no model-generated summary, extra paid summarization call, cross-thread
retrieval or implicit access to other member conversations. The system prompt
remains stable; the bounded conversation payload carries context provenance.
To bring older material back into a discussion, the owner can explicitly quote
it in a new message. Dedicated selection/retrieval controls are a later increment.

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

Management-first unsolicited routing, dedicated exact historical excerpt
selection, and local-first voice follow this text foundation. None is claimed
as complete by this change. Voice must reuse the exact conversation with explicit
consent, cancellation, provider choice, and usage boundaries.
