# Organization subscription setup

Eidolon's dedicated ChatGPT connection supports starter-team **Improve** and **Generate**. Consent, policy activation, generation, and creation are separate owner actions.

**Verification boundary:** tests use synthetic authorization, provider responses, and vault values. No actual account grants or live-provider validation were performed. Synthetic proof does not establish live OAuth compatibility or subscription entitlement.

## Scope and owner flow

Production protected storage requires local macOS and the authenticated local owner transport. Linux, Windows, and remote connections fail closed.

Consent, policy activation, and inference require organization/owner identity and eligible starter-team state without real work. Legacy-empty organizations may retain setup charges. Unsupported managed/shared constraints, unknown authority, source changes, and running/uncertain attempts can block these actions.

1. Open **Build your starter team**, **Check configuration**, and enter the work and success descriptions. Fresh profiles save names first.
2. Choose **Set up ChatGPT**, explicitly allow local storage, then **Continue with ChatGPT**. Complete system-browser consent and **Check connection**. Consent does not activate policy or send inference.
3. **Load account models** and select a returned **Setup model**. Existing Codex credentials are not imported or reused. This dedicated connection is bound to the installation, profile, organization, owner, issued client, and account.
4. Set Improve, Generate/Regenerate, total call limits, and local timeout. **Review setup policy**, including effective limits, retained calls, prior policy, and guarantees being retired. Acknowledge the changes and **Apply reviewed policy**.
5. **Load account models again**. Policy application changes the source revision and invalidates prior catalog context. Startup/status checks do not silently discover models, refresh credentials, or send inference.
6. Optionally **Improve** either description. Review/edit and **Apply suggestion**, or **Discard suggestion**. Input is not replaced automatically.
7. **Generate team** and review the proposal. If you edit it, choose **Review changes**. Then explicitly choose **Create team**. Generation does not save the team; creation neither starts work nor verifies its execution provider.

Inference revalidates owner, source, policy, connection, and account-model evidence. The execution-model configuration remains unchanged.

## Policy and accounting

The reviewed version-3 `subscription_calls` policy retires only the listed **setup** token, context, output, and cost guarantees, including the setup product token ceiling and applicable configuration inheritance. Execution constraints remain unchanged. Historical origins are reported as unknown where provenance is unavailable.

Effective limits respect the inherited call ceiling and timeout. Attempts, legacy charges, and lifetime debt survive policy changes, reconnects, cancellation, and disconnect; the preview shows remaining allowance.

There is **no hard token or USD cap**. Local cancellation/timeout cannot guarantee upstream usage stops. Valid token observations are reported; absent/invalid telemetry stays unknown, and plan cost remains unknown rather than zero.

Unknown telemetry differs from uncertain completion:

- A known terminal response may lack token usage. That alone does not block another eligible, explicitly requested call.
- A possibly dispatched request without a known terminal outcome remains uncertain. Running/uncertain attempts block new sends and policy changes; changing input or request identity cannot bypass this.
- Calls whose dispatch was durably admitted remain charged. There is no automatic inference retry, refund, or resend to discover the outcome. An exact completed result can replay locally without a provider send. Known terminal failure can permit a new explicit action within remaining limits.

**Check unsent request** requires exact original inputs and matching current owner, source, account, authorization epoch, and store revision. It locally authenticates the retained request and atomically closes a proven running/undispatched attempt with no dispatch marker or outcome, fencing delayed workers. An exact failed attempt can be rechecked. It needs no cached models and sends no provider request. Unavailable storage leaves it blocked; explicitly check again after release.

After renderer restart, re-enter exact original jobs, goals, and naming style and choose the **Original request** operation; form text is not persisted. **Find unsent request** reads ledger/current-owner/source metadata for a unique pending match. Its advisory result requires explicit **Check unsent request** before Retry. Missing/ambiguous evidence and dispatched/uncertain attempts remain blocked.

**Retry unsent request** requires server proof of a matching terminal failure before dispatch and current eligible allowance. Clicking this one-use offer starts one new normal admission with a new request identity; the old row and all existing debt remain. Dispatched/running/uncertain attempts, missing or invalid proof, and stale input/source/owner context cannot authorize it. Generic errors and status checks never rotate request identities or trigger retry.

If models need loading, explicitly choose **Set up ChatGPT → Load account models**. This invalidates the retry offer; once the old row is durably failed, ordinary explicit Improve/Generate uses normal admission.

## Connection recovery

**Check connection** reads status/policy without token exchange, refresh, inference, or recovery. Failed setup-editor mutations cause a setup re-read, never automatic resend.

Setup's **Refresh authorization**, available only for active connections, uses a durable single-admission ticket. Admitted/uncertain operations are not resent. Verified current completion may trigger one model-discovery request; historical receipts or old-source recovery cannot. Reload models when evidence is needed.

**Reconcile saved operation** checks an exact saved ticket or durable authorization-publication attempt. Publishing recovery authenticates the exact already-committed grant, or proves complete local retirement and terminalizes that exact host attempt. Missing or ambiguous proof stays blocked; local retirement leaves remote revocation unconfirmed. Authorization-publication recovery neither exchanges tokens nor fetches models. Recovery does not resend inference. Interrupted browser consent cannot resume from persisted PKCE secrets, which are memory-only.

**Disconnect ChatGPT** is available for active and refreshing connections. It retires local access and attempts remote revocation when permitted. Stranded-refresh retirement preserves evidence without resending refresh; remote revocation may be unavailable and remains unconfirmed. Team, history, and debt remain.

**Settings → Organization → ChatGPT setup connection** remains available after Create for status, explicit **Recover connection state**, and **Disconnect setup connection**. It adds no consent, refresh, model, policy, or inference authority. After mutation failure, check status explicitly before another action.

Reconnect through Home requires existing fresh-setup eligibility, explicit consent, an absent/disconnected store, and required cleanup; it is unavailable after Create or work begins. Returning consent must match the prior client/account. Its new authorization epoch requires model reload and reviewed policy rebinding. Refresh preserves the epoch while rotating token revision.

## Verification contract

The [native UI journey](../apps/desktop/e2e/subscription-native-journey.spec.ts) must cover fresh/legacy-empty consent, policy, Improve apply/discard, Generate, Create, post-Create Settings disconnect, and uncertain-result blocking. Native recovery gates must exercise real SQLite pre-dispatch writer contention and macOS ACL-descriptor identity refusal, retained-context unsent Check and explicit Retry, exact authorization-publication recovery, and preserved history/debt. Backend/component gates must also cover renderer-restart Find→Check and delayed-worker fencing. The [gateway journey](../tests/tui_gateway/test_subscription_native_journey.py) covers integration and network-denial guards.

Require real macOS Electron and production owner/auth/store/catalog/policy/admission/sender code, with synthetic consent, TLS provider edges, and vault primitive. Production filesystem/ACL checks remain; nonlocal network paths are denied. This does not prove real Keychain grants or billable inference.

Required durable [Organization regression](../.github/workflows/organization-regression.yml) integration:

- **Organization subscription native journey (macOS):** backend/store, gateway, component, type/build, native display, subscription UI, and ordinary API-route regressions. Required journeys must execute without skip/retry.
- **Organization desktop and native smoke (Linux):** the aggregate must also require the macOS subscription job. Its label does not imply Linux subscription support.

Cold and already-running old-runtime tests pin `43a84a77f094084ce8eb8dc2e99fdd679fb9d065` and require version-3 refusal without provider sends. Retain source receipts, logs, screenshots, traces, and reports. Verify passing checks and artifacts for the exact final revision; earlier runs are insufficient.

Implementation: [Settings lifecycle](../apps/desktop/src/app/eidolon/organization-setup-connection-client.ts), [owner actions](../apps/desktop/src/app/eidolon/use-team-setup-subscription.ts), [gateway](../tui_gateway/methods_organization_setup_subscription.py), [authorization/recovery](../eidolon_cli/organization_setup_subscription_auth.py), [policy](../eidolon_cli/organization_setup_subscription_policy.py), [admission/accounting](../eidolon_cli/organization_setup_subscription_admission.py).
