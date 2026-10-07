"""No-spend admission and complete, independently graded trial orchestration."""
import json
import os
from types import SimpleNamespace

import pytest

from scripts import eval_organization_semantics as evaluation


def test_unsupported_preflight_and_unknown_price_never_reach_credentials_or_provider(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(evaluation, 'native_preflight', lambda: {
        'outcome': 'unsupported', 'providerCalls': 0, 'credentialAccessed': False,
        'execution': {'status': 'unsupported', 'isolation': {'established': False}}})
    monkeypatch.setattr(evaluation, 'load_live_secret', lambda *_: pytest.fail('Credential access before admission'))
    monkeypatch.setattr(evaluation, 'synthetic_provider', lambda *_: pytest.fail('Provider started before admission'))
    monkeypatch.setattr(evaluation, 'run_trial', lambda *_args, **_kwargs: pytest.fail('Trial started before admission'))
    destination = tmp_path / 'unsupported'
    assert evaluation.main(['--scenario', 'signed_bucket', '--output-dir', str(destination)]) == 2
    report = json.loads((destination / 'report.json').read_text())
    assert report['providerCalls'] == 0 and report['credentialAccessed'] is False
    assert report['semanticCorrect'] is None and not report['taskSuccess']
    assert not (destination / 'evidence').exists()
    config = tmp_path / 'provider.json'
    config.write_text(json.dumps({'provider': 'custom', 'model': 'selected',
        'base_url': 'https://provider.example.invalid/v1', 'api_mode': 'chat_completions',
        'key_env': 'SELECTED_EXISTING_KEY'}))
    blocked = tmp_path / 'unpriced'
    assert evaluation.main(['--scenario', 'signed_bucket', '--output-dir', str(blocked),
        '--live-model', '--acknowledge-billing', '--provider-config', str(config), '--max-cost-usd', '1']) == 1
    assert not blocked.exists()
    capsys.readouterr()


@pytest.mark.linux_only
@pytest.mark.parametrize('scenario,retention', [
    ('duplicate_retention', 'earliest'), ('duplicate_retention', 'latest'), ('signed_bucket', 'earliest')])
def test_semantic_cli_retains_exact_independent_evidence_without_external_calls(tmp_path, monkeypatch, capsys, scenario, retention):
    import socket
    connect = socket.socket.connect
    calls = []

    def only_loopback(sock, address):
        assert isinstance(address, tuple) and address[0] in {'127.0.0.1', '::1'}, address
        calls.append(address)
        return connect(sock, address)

    monkeypatch.setattr(socket.socket, 'connect', only_loopback)
    monkeypatch.setenv('OPENAI_API_KEY', 'unselected-secret-must-not-appear')
    destination = tmp_path / (scenario + '-' + retention)
    code = evaluation.main(['--scenario', scenario, '--retention', retention, '--output-dir', str(destination)])
    report = json.loads((destination / 'report.json').read_text())
    capsys.readouterr()
    if report['preflight']['outcome'] == 'unsupported' and os.environ.get('EIDOLON_REQUIRE_PROJECT_SANDBOX') != '1':
        assert code == 2 and not calls and report['providerCalls'] == 0
        assert report['credentialAccessed'] is False and report['taskSuccess'] is False
        return
    assert code == 0 and report['outcome'] == 'passed', report
    assert report['semanticCorrect'] is True and report['taskSuccess'] is True
    assert report['applicationTaskSuccess'] is True and report['credentialAccessed'] is False
    assert report['retainedEvidence']['bundleRestoreVerified'] is True
    assert calls and report['providerCalls'] <= report['limits']['max_model_calls']
    assert report['baselineExecution']['status'] == ('failed' if scenario == 'signed_bucket' else 'passed')
    assert report['coverage']['restart'] is True
    assert report['coverage']['recovery' if scenario == 'signed_bucket' else 'clarification'] is True
    from scripts.organization_trial_evidence import verify_trial_artifact
    manifest = verify_trial_artifact(destination / 'evidence')
    assert manifest['scenarioSha256'] == report['scenarioSha256']
    for path in destination.rglob('*'):
        if path.is_file():
            assert b'unselected-secret-must-not-appear' not in path.read_bytes()
    with pytest.raises(ValueError, match='already exists'):
        evaluation.run(SimpleNamespace(live_model=False, acknowledge_billing=False,
            provider_config=None, max_cost_usd=None, scenario=scenario, retention=retention, output_dir=destination))


@pytest.mark.parametrize('boundary', ['driver', 'oracle', 'retention', 'interrupt'])
def test_exceptional_trials_preserve_private_state_without_publishing_secrets(tmp_path, monkeypatch, capsys, boundary):
    from contextlib import nullcontext
    secret = 'sensitive-value-never-in-report'
    monkeypatch.setattr(evaluation, 'native_preflight', lambda: {
        'outcome': 'passed', 'execution': {'status': 'passed', 'isolation': {'established': True}}})
    monkeypatch.setattr(evaluation, 'baseline_execution', lambda _: {
        'status': 'failed', 'testCount': 1, 'isolation': {'established': True}})
    monkeypatch.setattr(evaluation, 'synthetic_provider', lambda _: nullcontext((
        {'secret': secret, 'model': 'synthetic', 'base_url': 'http://127.0.0.1:1'}, {'calls': [], 'errors': []})))

    def execute(root, _route, _scenario, **_kwargs):
        (root / 'source').mkdir()
        (root / 'profile').mkdir()
        (root / 'profile' / 'ledger-marker').write_text(secret)
        if boundary in {'driver', 'interrupt'}:
            raise KeyboardInterrupt() if boundary == 'interrupt' else RuntimeError(secret)
        return {'taskSuccess': True, 'checks': {'serviceStopped': True}, 'coverage': {}, 'restart': {},
                'ownerActions': [], 'projectRuns': [{'stderr': secret}], 'deliveredFiles': {'app.py': secret}, 'sourceIntegration': {},
                'limits': {}, 'usage': {'modelCalls': 1}, 'stopReason': secret,
                'source': root / 'source', 'delivered_commit': 'commit', 'original_commit': 'base',
                'store': SimpleNamespace(settings=SimpleNamespace(read_roots=(str(root / 'source'),)))}

    def oracle(*_args, **_kwargs):
        if boundary == 'oracle':
            raise ValueError(secret)
        return {'semanticCorrect': True, 'status': 'passed'}

    def refuse_retention(*_args, **_kwargs):
        raise ValueError(secret)

    monkeypatch.setattr(evaluation, 'run_trial', execute)
    monkeypatch.setattr(evaluation, 'evaluate_delivered_commit', oracle)
    monkeypatch.setattr(evaluation, 'retain_trial_evidence', refuse_retention)
    destination = tmp_path / boundary
    result = evaluation.main(['--scenario', 'signed_bucket', '--output-dir', str(destination)])
    assert result == (130 if boundary == 'interrupt' else 1)
    report = json.loads((destination / 'report.json').read_text())
    assert report['taskSuccess'] is False and report['semanticCorrect'] is None
    assert report['privateRecovery']['shareable'] is False
    assert (destination / '.private-recovery' / 'profile' / 'ledger-marker').read_text() == secret
    assert not (destination / 'evidence').exists()
    assert 'deliveredFiles' not in report and 'projectRuns' not in report
    captured = capsys.readouterr()
    assert secret not in captured.out + captured.err + json.dumps(report)
    if boundary in {'driver', 'interrupt'}:
        assert report['providerCalls'] is None
