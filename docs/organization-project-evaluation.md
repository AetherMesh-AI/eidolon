# Controlled organization evaluation

These entrypoints exercise the real service, durable organization, provider
boundary, reviewed edits, isolated tests and local Git integration. They never
accept a production repository, push a result remotely, or deploy it.

## Prove the sandbox before any provider use

```sh
python scripts/eval_organization_project.py --preflight --report preflight.json
```

This runs one trusted tiny unittest snapshot through the existing Linux
bubblewrap/seccomp runner. It reads no credential, starts no provider and makes
zero model calls. A successful preflight is not a completed objective or a model
quality result. Unsupported isolation exits 2; another preflight failure exits 1.
There is no host-shell fallback and no automatic change to OS security settings.

Both evaluation entrypoints perform this check before credential access or
provider startup, including synthetic mode. Unsupported macOS/Windows or
restricted Linux hosts stop before spending model calls. Use an already
supported Linux environment; do not weaken protections to make the check pass.

## Existing pipeline smoke test

```sh
python scripts/eval_organization_project.py --report evaluation.json
```

The default is a deterministic loopback Chat Completions fixture, with no billed
provider calls or discovery of user provider configuration. Its
`deterministic_local_fixture` label means pipeline coverage, not real-model
intelligence. Proxy bypass is scoped to loopback fixtures and restored afterward;
live providers keep the caller's selected proxy settings.

The two-file addition objective deliberately supplies the exact replacement.
On a supported host, exit 0 requires exact source and unchanged tests, actual
isolated execution, separate review/acceptance identities and a verified local
Git branch. The temporary project is removed; its JSON report retains the tested
and delivered bytes and receipts. Unsupported preflight instead reports
`objectiveStatus: not_started`, zero provider calls and no source integration.

## Semantic trials and recovery coverage

```sh
python scripts/eval_organization_semantics.py \
  --scenario duplicate_retention --retention earliest --output-dir trial-earliest
python scripts/eval_organization_semantics.py \
  --scenario duplicate_retention --retention latest --output-dir trial-latest
python scripts/eval_organization_semantics.py \
  --scenario signed_bucket --output-dir trial-repair
```

Each command creates a fresh fixture/profile. The output directory must not
already exist. These defaults still use a clearly labeled synthetic provider;
all plans and approvals from it are scripted runtime tests, not intelligence
results. Live mode uses the real executor at every model stage.

- **Duplicate retention:** the brief intentionally omits which duplicate record
  survives. The driver records the predeclared earliest/latest owner answer only
  in response to a matching clarification, reopens the same durable service at
  that pending boundary, and verifies exact response replay is idempotent.
  Editing before the answer does not establish clarification coverage.
- **Signed integer buckets:** the active mathematical specification and public
  tests expose a genuine initial failure without prescribing the repair. A
  pre-model baseline executes those trusted starting bytes. The synthetic
  provider deliberately produces a wrong first candidate, then a corrected one,
  to exercise a real failed test, one bounded replan, restart and successful
  second run. A real model that fixes it first try can pass the task while
  `coverage.recovery` remains false.

Only predeclared owner answers and one permitted replan after a genuine failed
project test are automated. Unexpected questions/interventions stop for review;
matching is a bounded fixture-specific text rule, not a general semantic judge.
The driver does not approve permissions, expand grants, amend scope, weaken
criteria or feed private-oracle feedback back into the model.

### Independent grading

Scenario and oracle identities are frozen before provider calls. Oracle files
are outside the agent's read roots and submitted context. After application
acceptance, exact delivered Git-commit bytes run inside the existing sandbox.
The sandbox emits bounded function observations; trusted host code compares data
against frozen expected values, which are not mounted into the sandbox. Host
code never imports or executes a generated implementation.

Calibration checks cover both correct implementations and semantic mutants that
pass public tests, including a mutant that replaces unittest assertions. A
passed unittest status without complete matching observations is insufficient.
These finite cases are not adversarial-proof: code shares an interpreter with
the observation collector and may attempt introspection/output forgery.

Reports separate application acceptance, independent `semanticCorrect`, overall
`taskSuccess`, clarification, failed-test recovery, restart and budget usage.
Exit 0 requires the bounded task and independent oracle to pass; exit 1 indicates
failure, exit 2 unsupported isolation, and exit 130 interruption. A recovery
coverage flag is not required merely to accept a first-try-correct repair.

### Retained evidence and interrupted runs

Successful sealing writes `evidence/` with a consistent SQLite backup, immutable
artifact/receipt exports, context and usage audits, owner resolutions, scenario
and oracle hashes, before/after manifests, and a Git bundle verified by restoring
it into a fresh repository. The bundle can be inspected without the original
working directory. Read-only files plus SHA-256 checks expose accidental changes;
they are not an external signature or protection against deliberate replacement.

Known credential bytes and recognizable credential patterns cause export to be
refused rather than silently editing exact evidence. Profile configuration,
environment values and raw provider wire logs are excluded. Unknown/transformed
secrets cannot be guaranteed detectable; review an artifact before sharing it.

Exceptions, interruption, failed shutdown or rejected export preserve the private
workspace at `.private-recovery/` and record a sanitized failure report. Do not
upload that directory: raw logs may contain sensitive data. It is removed only
after consistent, credential-screened evidence and a verified Git bundle are
sealed. Unknown send counts stay unknown; interrupted calls are not refunded.

## Explicit opt-in to a real provider

Real inference is a separate spending/data-transmission decision. Choose the
exact provider endpoint/model and a total ceiling before running either command.
The credential must already be available in the current secret scope, or an
explicitly exported user-side environment variable for a standalone invocation.
Do not put keys in the JSON, chat, or newly created CI secrets.

Use a credential-free JSON file with exactly these fields. Replace every
placeholder with your deliberately selected values and verified conservative
price ceilings; the placeholders are intentionally not runnable:

```json
{
  "provider": "custom",
  "model": "YOUR_EXACT_MODEL_ID",
  "base_url": "https://YOUR_PROVIDER_ENDPOINT/v1",
  "api_mode": "chat_completions",
  "key_env": "YOUR_EXISTING_API_KEY_VARIABLE",
  "model_costs": [{
    "provider": "custom",
    "model": "YOUR_EXACT_MODEL_ID",
    "input_usd_per_million": "YOUR_INPUT_PRICE_CEILING",
    "output_usd_per_million": "YOUR_OUTPUT_PRICE_CEILING"
  }]
}
```

```sh
python scripts/eval_organization_semantics.py \
  --scenario duplicate_retention --retention earliest --output-dir live-trial \
  --live-model --acknowledge-billing --provider-config evaluation-provider.json \
  --max-cost-usd YOUR_TOTAL_USD_CEILING
```

The same four live-policy arguments work with the smoke entrypoint. Unknown,
missing or mismatched pricing is rejected before credential access; there is no
unlimited-price fallback. Explicit zero rates are a declared known ceiling, not
an automatic claim that the selected service is free. Live endpoints must use
HTTPS without URL credentials, query strings or fragments.

Only the one named existing credential enters the disposable profile in memory.
No default-profile, OAuth, cloud-SDK, key-command, credential-pool or other-model
fallback is authorized. Existing profiles and grants remain unchanged.

### Bounds and assumptions

- Smoke: 16 model sends, 1,048,576 reserved input/output tokens, one project run,
  no revisions or replans.
- Semantic trial: 24 model sends, 1,572,864 reserved input/output tokens, one
  revision, one replan, at most three owner resolutions and two project runs.
- Both: 65,536 context-token ceiling, 2,048 output tokens per model call,
  60-second stage limit and 180-second objective deadline, including time waiting
  for owner input. Semantic trials separately permit one trusted preflight, one
  trusted baseline and one independent-oracle execution.

The durable reservation ledger charges each physical send its conservative input
plus output limits using the exact configured route prices. Retries, interrupted
calls and unreported usage retain their reservations. It refuses the next send
when call, token, deadline or configured USD admission limits would be exceeded.
Provider fees can differ from supplied ceilings; these bounds are not an invoice
guarantee. Cancellation does not retract an already dispatched provider request.

A reported failure does not authorize changing provider, raising budgets,
weakening gates or automatically rerunning the trial. No real-model capability
claim is justified by the synthetic development suite alone.
