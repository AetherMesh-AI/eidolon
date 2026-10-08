# Internal agent conversations

Persistent Executives, Managers and Workers can ask a named colleague for context
without opening a formal decision or permission request. These are local,
profile-scoped conversations, not email, Slack, Discord, or outbound messages.
The Organization screen and each participant's inspector show the retained thread,
exact sender and recipient, message body, runtime read state and waiting recipient.
Inspecting a conversation as the owner does not mark it read by an agent.

## Runtime behavior

During an assigned stage, the agent receives a public `messagingDirectory` of
eligible exact identities and participant-only `internalConversations` for that
objective. It can return only a bounded message proposal:

```json
{"messages":[{"recipientId":"research-manager","subject":"Audience",
"body":"Which audience is this brief for?"}]}
```

An optional `threadId` continues the same two-person thread and exact subject.
The backend supplies sender identity, objective/task/project linkage and timestamps
from the owned lease; a model cannot supply or spoof those values. Sending suspends
that assignment. The backend queues a tool-free `request.message` turn on the exact
recipient's existing identity. The recipient returns `{"reply":"…"}` or an
intervention. It cannot recursively send messages, raise formal requests, change
memory, use tools, approve work, or report completion during that delivery.
After all its replies are durable, the original assignment resumes on the original
sender. The sender may then raise a formal question, decision, staffing, or permission
request through the ordinary authorization flow if it needs an actual decision.

Each stage may send at most four messages cumulatively, each thread at most eight
exchanges, and each objective at most 24 exchanges. Subject and body limits are
200 and 6,000 characters. Identical re-sends from the same stage are rejected;
expired claims and duplicate finishes cannot append a second reply. All model
turns consume the existing objective stage, model-call, token, cost and deadline
budgets. A reply cannot create an endless chain of automatic acknowledgements.

## Communication policy and privacy

`organization.communication_scope` is persisted and included in policy fencing:

- `disabled`: no internal messages.
- `same_team`: enabled identities in the same department only.
- `collaborators` (default): same department, two existing participants assigned
  to this objective, or an explicit Manager/Executive `managed_teams` route.

An inactive, disabled, removed, or out-of-scope recipient is never silently replaced
or reactivated. An undeliverable message becomes a visible intervention. Restore
its exact permitted recipient and retry within the ordinary retry budget, or cancel
the objective. Owner cancellation invalidates outstanding deliveries and replies.
Changing communication scope fences in-flight calls just like other policy changes.
Historical participant names, teams and IDs remain in the thread after retirement.
An explicit owner task handoff may move the waiting assignment to a replacement
agent, but it does not silently add that replacement to the original conversation
or forward its private replies. The replacement resumes with its own authorized
context; conversation membership and original recipients remain unchanged.

Delivery includes only the selected text, this two-person thread, and the recipient's
minimal identity. It does not automatically attach the sender's task description,
objective, owner inputs, evidence, files, tools, grants, directory, or private memory.
Private memory is not project-tagged, so **neither participant's private memory is
loaded for reply generation**. If both identities already participate in this
objective, up to 6,000 characters from the recipient's own completed work may be
supplied as explicitly scoped context. Otherwise there is no recalled work context.
Objective linkage is redacted for a recipient outside the objective; task/project
linkage is further limited to identities assigned to that task. Owner inspection
retains the complete provenance.

Messages are untrusted context. Their content, even if it says “approved,” grants
no authority and is not a review, tool receipt, formal clarification, or completion
evidence. Models still choose message text, so data minimization at the delivery
input boundary is a safeguard, not a claim that generated prose can never contain
sensitive information. Do not put secrets in tasks or messages.

## Validation

The permanent organization regression workflow discovers the ledger, privacy,
restart, scope and synthetic local-provider tests under `test_organization_conversation*`.
The wire test runs the production executor through worker message → exact recipient
reply → restarted original worker completion, with three persisted model-call
reservations. No live account, network model call, or external message is needed.
Desktop behavioral and native Electron checks cover owner inspection separately.
