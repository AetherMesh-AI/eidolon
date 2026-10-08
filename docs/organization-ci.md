# Organization regression CI

`.github/workflows/organization-regression.yml` retains organization checks on pull
requests to `main`, pushes to `main`, and explicit manual dispatch. It supplements
rather than replaces the inherited `CI` workflow or its protected aggregate gate.
No branch protection, repository setting, secret, or permission grant is changed.

## Scope and execution

- Linux backend: all `test_organization*.py` files in the CLI, RPC gateway,
  messaging gateway, tools and scripts suites, plus the organization skill
  namespace contract. This includes legacy schema migration, clarification and
  recovery, live policy changes, exact execution receipts, multi-repository
  acceptance, attention and durable outcomes. Executive work-package contracts
  add atomic decomposition, exact manager routing, shared allocations, dependent
  execution, old-objective migration and explicit package handoffs. Shared
  config/env/managed-scope and gateway import compatibility suites cover the strict reader’s pure primitives.
  Profile export and recovery-backup suites exercise metadata-only registration
  portability and SQLite WAL snapshots. Uses the canonical per-file runner.
- macOS and Windows: a deliberately smaller native ledger, migration, recovery,
  receipt, policy and project-execution guard suite, including the durable
  work-package and scripted executor lifecycle contracts. Spawned-process config
  tests cover delayed reopen/startup/watchers, invalid-file pauses and idempotent repair
  without replaying claims or resetting model-call reservations. The unchanged
  large artifact-retention lifecycle has its own file so short ledger contracts
  do not consume its per-file timeout on slower Windows runners; no timeout or
  assertion is relaxed. Ledger registration tests kill actual subprocesses before
  and after commit, race stale writers, retain definitions through restart and
  revoke changed authority. Export/import and recovery snapshots run on all hosts;
  symlink/junction retarget cases execute only on their actual supported OS.
  These hosts verify honest
  unsupported execution; they do not claim Linux sandbox support.
- Linux desktop: organization UI/store/shell tests and synthetic fixture helpers,
  full desktop type checks and production build, followed by the organization
  Electron specs under Xvfb, one worker with retries disabled. The specs cover
  setup, requests/attention, reconnect/restart, selected repositories, isolation,
  cancellation/budgets, source acceptance and retained outcome evidence. Project
  registration covers deliberate confirmation, restart persistence and revocation
  while asserting that fixture YAML stays unchanged. Providers
  are local deterministic fixtures, not live model calls. Package UI tests cover
  current/historical ownership, task provenance, acceptance separation, exact
  handoff selections and legacy admission guidance. The completion, request,
  owner and project fixtures include executive decomposition; native completion
  asserts that completed packages alone cannot produce an accepted objective.
  See [work-package design and rollout](organization-work-packages.md).

Fast component/type failures stop the desktop job before native installation,
production build and Electron. The backend and OS guard jobs run independently
so one failure does not hide other platforms. All runners are standard hosted
runners; no larger runner labels are required.

Changes to any Python file, desktop/shared frontend files, root dependency manifests, the canonical
runner, these CI helpers, workflow or this guide trigger the lanes. The broad
Python match is intentional: organization behavior imports shared agent, state,
configuration, gateway and tool code. Pure unrelated documentation/site/assets
changes do not start this workflow. GitHub path filtering uses its normal diff
limits; manual dispatch is available when a large change needs explicit evidence.
The workflow does not replace a required check because path-skipped workflows may
remain pending under branch protection.

Only superseded PR runs are cancelled. Main runs use unique concurrency groups so
each merge retains its own evidence. Native test reports and traces are retained
for seven days under a run-and-attempt-specific artifact name.

## Reproduce

Use Python 3.12 and Node 24. Install from the committed lockfiles:

```sh
python -m pip install uv==0.12.23
uv sync --locked --python 3.12 --extra dev --extra slack
uv pip install --no-deps pytest-timeout==2.4.0
npm ci
```

Runtime and development dependencies are taken from `uv.lock`; the timeout plugin
is an exact-pinned CI tool installed without dependency resolution. The Linux
compatibility lane includes the locked Slack extra for gateway HTTP/API and
Slack configuration imports; it does not connect a Slack account. `npm ci` uses
the root npm lockfile. Actions are SHA-pinned, GitHub-owned, and have only
`contents: read`; checkout does not persist credentials. No secrets are inherited.

On a supported Ubuntu 22.04 native host, install verified upstream Bubblewrap 0.13:

```sh
bash scripts/ci/organization-sandbox.sh
EIDOLON_REQUIRE_PROJECT_SANDBOX=1 HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/eidolon_cli/test_organization*.py tests/tui_gateway/test_organization*.py \
  tests/gateway/test_organization*.py tests/tools/test_organization*.py \
  tests/scripts/test_organization*.py tests/agent/test_org_skill_namespace.py -j 4
```

The installer verifies the release SHA-256 before building. It does not change
security sysctls, enable privileged containers, or substitute unsandboxed project
execution. `EIDOLON_REQUIRE_PROJECT_SANDBOX=1` turns unavailable native isolation
into test failure. On a restricted local container, omit this variable only for
partial unsupported-path testing; skipped native contracts are not CI proof.
The Windows/macOS guard command is listed directly in the workflow.

```sh
cd apps/desktop
npx vitest run src/app/eidolon src/components/ui/confirm-dialog.test.tsx \
  src/components/ui/confirm-dialog-unmount.test.tsx src/store/organization-work.test.ts \
  src/app/contrib/organization-shell.test.tsx e2e/organization-*.unit.test.ts
npm run typecheck
npm run build
CI=true EIDOLON_REQUIRE_PROJECT_SANDBOX=1 xvfb-run -a \
  npx playwright test e2e/organization-*.spec.ts --workers=1 --retries=0
```

## Cost and evidence

Each matching commit runs four jobs: one Linux backend, one Linux desktop, one
macOS core and one Windows core. Hard job limits are 20, 25, 15 and 15 minutes,
respectively (75 aggregate runner-minutes before billing multipliers). These are
ceilings, not observed consumption or a price quote. Billing depends on the
repository plan, included minutes and platform rates. There is no scheduled run,
paid model call, service credential, or new runner class.

The earlier native fixture workflow [run 37745547780](https://github.com/AetherMesh-AI/eidolon/actions/runs/37745547780)
completed in about 5 minutes 16 seconds of wall time across parallel jobs. It used
a different dependency install and a narrower desktop selection; it is provenance
for the supported sandbox setup, not a timing claim for this permanent workflow.
Use this workflow's Actions job durations to measure actual cost after changes.
