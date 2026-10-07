#!/usr/bin/env python3
"""Run bounded semantic trials; real inference requires explicit priced opt-in.

The default provider is a labeled synthetic loopback fixture. Frozen independent
oracles grade delivered bytes after application acceptance. No generated code is
executed on the host, and no credential is read before native sandbox admission.
"""
from __future__ import annotations

import argparse
from contextlib import nullcontext, redirect_stdout
import hashlib
import json
from pathlib import Path
import sys
import shutil

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.organization_trial_driver import TRIAL_LIMITS, run_trial, synthetic_provider
from scripts.organization_trial_evidence import retain_trial_evidence
from scripts.organization_trial_oracle import (
    evaluate_delivered_commit, freeze_scenario_oracle, scenario_digest,
)
from scripts.organization_trial_policy import (
    COST_LIMITATION, add_live_arguments, load_live_secret, native_preflight, selected_live_route,
)
from scripts.organization_trial_scenarios import get_scenario


def parser():
    result = add_live_arguments(argparse.ArgumentParser(description=__doc__))
    result.add_argument('--scenario', choices=('duplicate_retention', 'signed_bucket'), required=True)
    result.add_argument('--retention', choices=('earliest', 'latest'), default='earliest',
                        help='Predeclared owner answer for duplicate_retention; never supplied before clarification.')
    result.add_argument('--output-dir', type=Path, required=True,
                        help='New write-once directory retaining the summary and portable evidence.')
    return result


def baseline_execution(scenario):
    """Run only frozen, trusted starting fixtures before any credential access."""
    from eidolon_cli.organization_project_runner import ProjectExecutionGrant, run_project_tests
    files = [{'path': 'root0/' + path, 'content': content, 'revision': 0,
              'sha256': hashlib.sha256(content.encode('utf-8')).hexdigest()}
             for path, content in sorted({**scenario.source_files, **scenario.public_test_files}.items())]
    return run_project_tests(files, ProjectExecutionGrant())


def _base_report(scenario, live, preflight):
    return {
        'schemaVersion': 1, 'mode': 'live_model' if live else 'synthetic_runtime_trial',
        'billing': 'explicitly_acknowledged_provider_calls' if live else 'nonbillable_loopback_only',
        'scenarioId': scenario.scenario_id, 'scenarioSha256': scenario_digest(scenario),
        'outcome': 'unsupported', 'taskSuccess': False, 'semanticCorrect': None,
        'coverage': {'clarification': False, 'recovery': False, 'restart': False},
        'preflight': preflight, 'baselineExecution': None, 'limits': dict(TRIAL_LIMITS),
        'providerCalls': 0, 'credentialAccessed': False,
        'sandboxRunLimits': {'preflight': 1, 'baseline': 1, 'objective': TRIAL_LIMITS['max_project_runs'], 'oracle': 1},
        'limitations': [COST_LIMITATION,
            'Synthetic provider output measures runtime coverage only, not real-model understanding.',
            'First-try correct work is success but does not establish failed-test recovery.',
            'Finite hidden cases are independent semantic observations, not a guarantee about arbitrary tasks.'],
    }


def _run_admitted(root, destination, scenario, selected, report, frozen):
    report['phase'] = 'credential_admission'
    report['credentialAccessed'] = selected is not None
    route = load_live_secret(selected, report['preflight']) if selected else None
    provider = nullcontext((route, None)) if route else synthetic_provider(scenario)
    with provider as (route, diagnostics):
        report.update(phase='organization_execution', providerCalls=None)
        raw = run_trial(root, route, scenario, live=selected is not None)
        public_keys = ('taskSuccess', 'checks', 'coverage', 'restart', 'ownerActions', 'projectRuns',
                       'deliveredFiles', 'sourceIntegration', 'limits', 'usage', 'stopReason')
        report.update({key: raw[key] for key in public_keys})
        report['providerCalls'] = raw['usage']['modelCalls']
        report['route'] = {key: route[key] for key in ('model', 'base_url', 'max_cost_usd', 'model_costs') if key in route}
        report['provider'] = 'custom'
        report['phase'] = 'independent_oracle'
        report['applicationTaskSuccess'] = raw['taskSuccess']
        report['oracle'] = evaluate_delivered_commit(
            raw['source'], raw['delivered_commit'], frozen, app_completed=raw['taskSuccess'],
            delivered_paths=[*scenario.source_files, *scenario.public_test_files],
            read_roots=raw['store'].settings.read_roots)
        report['semanticCorrect'] = report['oracle']['semanticCorrect']
        clarification_ok = scenario.owner_answer is None or raw['coverage']['clarification']
        passed = raw['taskSuccess'] and report['semanticCorrect'] is True and clarification_ok
        report['applicationTaskSuccess'] = raw['taskSuccess']
        report['taskSuccess'] = bool(passed)
        report['outcome'] = 'passed' if passed else 'unsupported' if report['oracle']['status'] == 'unsupported' else 'failed'
        if diagnostics is not None:
            report['syntheticDiagnostics'] = {'calls': diagnostics['calls'], 'errors': diagnostics['errors']}
            if diagnostics['errors']:
                report['outcome'] = 'failed'
                report['taskSuccess'] = False
        report['phase'] = 'evidence_retention'
        report['retainedEvidence'] = retain_trial_evidence(
            destination / 'evidence', store=raw['store'], source=raw['source'], scenario=scenario,
            frozen_oracle=frozen, original_commit=raw['original_commit'], delivered_commit=raw['delivered_commit'],
            report=report, service_stopped=raw['checks']['serviceStopped'], forbidden_values=(route['secret'],))
    return report


def run(args):
    # Missing billing or pricing choices fail before artifact creation, sandbox
    # work, credentials or any provider construction.
    selected = selected_live_route(args)
    scenario = get_scenario(args.scenario, retention=args.retention)
    destination = args.output_dir.resolve()
    if destination.exists():
        raise ValueError('Output directory already exists; choose a new write-once destination')
    destination.mkdir(parents=True, mode=0o700)
    preflight = native_preflight()
    report = _base_report(scenario, selected is not None, preflight)
    if preflight['outcome'] != 'passed':
        report['outcome'] = preflight['outcome']
        return destination, report
    # The private workspace survives exceptions and interruption. It is never
    # part of the shareable evidence archive: logs may contain unredacted data.
    root = destination / '.private-recovery'
    root.mkdir(mode=0o700)
    report['phase'] = 'freeze_oracle'
    try:
        frozen = freeze_scenario_oracle(destination / 'oracle', scenario, read_roots=(str(root / 'source'),))
        report['phase'] = 'baseline_execution'
        baseline = baseline_execution(scenario)
        report['baselineExecution'] = baseline
        baseline_observed = (baseline['status'] == scenario.baseline_expected
            and baseline['testCount'] > 0 and baseline['isolation']['established'] is True)
        if not baseline_observed:
            report.update(outcome='unsupported' if baseline['status'] == 'unsupported' else 'failed',
                          stopReason='The trusted starting fixture did not produce its required baseline outcome.')
        else:
            report = _run_admitted(root, destination, scenario, selected, report, frozen)
        if report.get('retainedEvidence', {}).get('bundleRestoreVerified') is True:
            shutil.rmtree(root)
            report['phase'] = 'complete'
    except (Exception, KeyboardInterrupt) as error:
        # Exact-export rejection may mean any copied model/output field contains
        # a credential. Never publish the accumulated report on that path.
        calls = report.get('providerCalls')
        accessed = report.get('credentialAccessed')
        phase = report.get('phase')
        report = {
            'schemaVersion': 1, 'mode': 'live_model' if selected else 'synthetic_runtime_trial',
            'scenarioId': scenario.scenario_id, 'scenarioSha256': scenario_digest(scenario),
            'outcome': 'cancelled' if isinstance(error, KeyboardInterrupt) else 'failed',
            'taskSuccess': False, 'semanticCorrect': None,
            'providerCalls': calls if type(calls) is int and 0 <= calls <= TRIAL_LIMITS['max_model_calls'] else None,
            'credentialAccessed': accessed if type(accessed) is bool else None,
            'phase': phase if phase in {'freeze_oracle', 'baseline_execution', 'credential_admission',
                                      'organization_execution', 'independent_oracle', 'evidence_retention'} else 'unknown',
            'stopReason': 'Trial interrupted or exact evidence processing refused; private recovery state was preserved.',
        }
    if root.exists():
        report['privateRecovery'] = {'directory': '.private-recovery', 'shareable': False,
            'warning': 'Do not upload or share this raw workspace. It may contain unredacted logs or sensitive data. '
                       'Use it only for local recovery and credential-screened evidence export.'}
    return destination, report


def main(argv=None):
    cli = parser()
    args = cli.parse_args(argv)
    try:
        with redirect_stdout(sys.stderr):
            destination, report = run(args)
    except Exception as error:
        # Configuration and evidence boundaries produce bounded safe errors.
        # Never echo arbitrary provider/transport exceptions or route secrets.
        print(json.dumps({'outcome': 'failed', 'failureType': type(error).__name__,
                          'reason': 'Trial setup or exact evidence retention was refused. No success is claimed.'}), file=sys.stderr)
        return 1
    (destination / 'report.json').write_text(json.dumps(report, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2, sort_keys=True))
    return {'passed': 0, 'failed': 1, 'unsupported': 2, 'cancelled': 130}[report['outcome']]


if __name__ == '__main__':
    raise SystemExit(main())
