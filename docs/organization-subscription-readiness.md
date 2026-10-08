# Subscription-backed organization readiness

Status checked October 8, 2026: **blocked, not implemented or activated**.
Ordinary Codex chats remain supported. This concerns the stricter organization
executor, whose model output must pass through organization grants and retained
evidence before any action. No sign-in, credential discovery or inference occurs
when running:

```sh
python -m eidolon_cli.organization_subscription
```

The JSON report describes documented capability, not the selected account's
entitlements. Unknown allowance and USD cost remain `null`, never zero.

## Why the existing Codex transport remains blocked

The [app-server protocol](https://developers.openai.com/codex/app-server)
supports an agent runtime, including native command, file, MCP and child-agent
operations. Its approvals do not mediate every read or every action. A read-only
sandbox, disabling shell alone, or asking the model to produce only text does
not prove that all actions go through Eidolon's organization grants. An event
arriving after native execution is not a pre-execution guard.

The local Codex 0.159.2 experimental schema was inspected without sign-in or
inference. No complete tool-deny capability was established. Both organization
route admission and the app-server turn entry point therefore remain blocked.

## A supported direction, with a separate hard blocker

OpenAI documents [ChatGPT-plan inference through public Responses](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference).
A direct, tool-free request would avoid a native agent subprocess. A future
adapter would submit exact input context and accept only a completed response;
partial output, interruption and allowance failure cannot become evidence of
completed work. There is no such adapter in this change.

The current [preview limitations](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations)
reject `max_output_tokens`. The organization currently reserves a finite input
and output ceiling before every physical send, including interrupted attempts.
This API cannot honor that output ceiling. Limiting received text, closing an
SSE connection, a timeout, or limiting the number of calls does not bound the
provider's generated tokens or consumed subscription allowance. Do not remove
the limit silently, invent zero-dollar pricing, fall back to a billable API,
or describe subscription capacity as free/unlimited.

## Authorization and future acceptance criteria

The documented [open-source registration flow](https://developers.openai.com/siwc/token-sharing-open-source/sign-in)
uses dynamic first-time client registration followed by the user's explicit
authorization. Eidolon would need its own account/workspace-bound registration,
host identity and validated OAuth grant. Existing Codex credentials must not be
copied or repurposed. The flow still depends on current account and workspace
eligibility; this diagnostic cannot verify that. No registration or grant is
created here.

Before offering this route, implement and review the dedicated sign-in lifecycle,
account selection, renewal and revocation; verify current provider capability;
and integrate durable usage admission, cancellation, bounded response storage,
exact context and terminal evidence with the organization executor.

If the provider still lacks a hard token cap, a separately designed allowance
mode could bound call count and local runtime, but cannot promise a hard token
or subscription-usage ceiling. It would need an explicit owner choice and clear
disclosure of that limitation, separate accounting from USD reservations, no
automatic paid fallback, and independent lifecycle/security tests. It is not an
option enabled by this change. Until those conditions are met, use the existing
explicitly configured bounded direct API route for organization work.
