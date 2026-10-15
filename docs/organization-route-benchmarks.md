# Offline route benchmark contract

`eidolon_cli.organization_route_benchmarks.evaluate_records(records)` is a pure,
bounded Python evaluator for already-authorized saved evidence. It accepts at
most 1,000 exact-schema records, does not open files or databases, and has no
network, provider, credential, scheduling, or routing integration.

Every record identifies task/input/output/contract/configuration by SHA-256,
route/provider/model/reasoning, task stage, difficulty bucket, and origin. Origins
are `saved_model_output` or `synthetic_fixture`, kept in separate cohorts. Neither
label authenticates how the caller obtained the output. No input/output content,
paths, endpoints, credentials, or additional fields are accepted.

Criteria require exact stable IDs and revisions. Supplied verification records
must bind the same output and contract hashes, evidence hashes, and an explicit
verifier ID/version. The evaluator never guesses verifier identity from a check
kind. Cohorts require the same task, input, contract, difficulty, stage and
versioned verifier set. Missing verifications and unknown results remain unknown.
A declared success requires every supplied criterion to pass before it counts as
reported success. These are caller-supplied reports, not independent attestation.

## Completion cost

Generation, repair and review each require an explicit accounting record:
completeness, attempt count including retries, billed USD or null, estimated USD
or null, and the pricing version for any estimate. A genuinely unused phase may
have a complete zero-attempt, zero-cost record; absence is never automatically
zero. Estimated cost should be calculated from genuine provider counts and the
explicit versioned configured prices. Estimates must never be copied into billed
cost fields. This evaluator validates the shape, not external billing truth.

A complete supplied accounting window sums all three phases. Reported cost per
success includes costs of failed samples as well as successful samples, divided
by reported successes. Any incomplete outcome or missing phase cost makes that
cohort's total and cost-per-success unknown. No successes produces null, not a
free or infinitely good route. Billed and estimated calculations stay separate.
These descriptive values are not confidence-adjusted predictions of future cost.

## Promotion boundary

All outputs are labeled `caller-supplied-offline-unattested`, with
`productionEligible: false` and `selectionChanged: false`. Synthetic records test
only the evaluator pipeline. Saved genuine model outputs require a future trusted
runtime verifier adapter, authenticated artifact lineage, predeclared comparable
held-out tasks, enough samples and uncertainty analysis before shadow ranking is
meaningful. The current typed-outcome receipts bind criteria and evidence hashes
but do not yet carry a separately versioned trusted verifier identity.

No live experiments are run. Future paid trials require explicit budget approval,
authorized data/provider scope and fixed spending limits. Production routing,
Fixed assignments, Auto eligibility, context requirements and reserves are unchanged.

Expected criterion sets are also a cohort dimension, even when verification is
missing. If phase-by-phase estimate pricing versions differ within a route's
samples, `estimatePricingComparable` is false and estimated aggregates remain
null. Billed-cost accounting is independent of those configured price versions.
