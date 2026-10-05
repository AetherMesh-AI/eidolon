# Controlled project evaluation

Run the real organization service/provider/edit/review/test/integration pipeline
against a disposable, two-file addition project:

```sh
python scripts/eval_organization_project.py --report evaluation.json
```

The default uses a deterministic loopback Chat Completions fixture, makes no
billable provider calls, and does not read your provider configuration or keys.
Its report is explicitly labeled `deterministic_local_fixture`; it validates the
application path, not real-model quality. No pytest installation is needed.
Loopback proxy bypass is scoped to the fixture lifetime and restored afterward;
live-provider runs retain the caller's deliberate proxy settings.

The fixed `python_unittest` recipe requires the supported Linux OS-isolation
backend. Unsupported hosts retain a real failure receipt and exit 2, never fall
back to running project code directly on the host, and never integrate source.
Exit 0 requires exact delivered source and unchanged tests, actual isolated test
execution, independent review and acceptance, and a verified new Git branch.
Other unsuccessful outcomes exit 1. The temporary project/profile/branch are
removed; the JSON retains exact tested and delivered files, test output, reviews,
integration receipt, acceptance, limits and usage. Fixture identifiers/timestamps
vary; the provider responses and scenario are deterministic.

## Explicitly opt in to a real model

Only intentionally configured direct Chat Completions API providers are supported
by this entrypoint. Create a credential-free JSON file with exactly these keys,
using your existing provider endpoint, model identifier and credential variable:

```json
{
  "provider": "custom",
  "model": "YOUR_EXACT_MODEL_ID",
  "base_url": "https://YOUR_PROVIDER_ENDPOINT/v1",
  "api_mode": "chat_completions",
  "key_env": "YOUR_EXISTING_API_KEY_VARIABLE"
}
```

```sh
python scripts/eval_organization_project.py \
  --live-model --acknowledge-billing \
  --provider-config evaluation-provider.json --report live-evaluation.json
```

This command may incur provider charges. Both flags and the explicit file are
required. It authorizes `read_file`, `patch`, `run_tests` and `integrate_source`
for this disposable fixture only, with the same exact grants on its worker.
There is no production repository argument, remote push or deployment.

The named credential must already be available through the current secret scope
(or explicitly exported environment variable for a standalone invocation).
Missing credentials fail closed: no default-profile, OAuth, cloud SDK, key-command,
credential-pool or other-provider fallback. The disposable profile receives only
that selected credential in memory. Keys are never copied into configuration,
`.env`, reports or other files. Existing profiles and grants are not changed.

One run is bounded to 16 model calls, 1,048,576 reserved total tokens, 65,536 tokens
per context, 2,048 output tokens per call, a 180-second objective deadline,
60-second per-stage timeout and one ten-second test recipe run. Calls/reservations
are finite, but these are not a currency cap or an invoice. No automatic repair,
replan or profile-grant expansion occurs. A live failure is a reported evaluation
result, not permission to select a different provider or weaken the gates.
