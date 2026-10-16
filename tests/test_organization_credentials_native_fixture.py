"""Acceptance setup remains explicit, synthetic, and metadata-only."""
import json
import sys

import pytest

from scripts.ci import organization_credentials_native_fixture as fixture
from eidolon_cli.organization_credential_security import CredentialError, ValueBinding


def test_fixture_seed_observe_and_synthetic_backend_never_write_values(tmp_path):
    (tmp_path / 'config.yaml').write_text('{}')
    seeded = fixture.seed(tmp_path)
    assert set(seeded) == {'organizationId', 'ownerId', 'agentId', 'identityId'}
    observed = fixture.inspect(tmp_path)
    assert observed['catalog']['entries'] == []
    assert observed['attempts'] == observed['modelCalls'] == observed['receiptCount'] == observed['auditCount'] == 0
    assert not (tmp_path / 'organization' / 'credentials.db').exists()
    backend = fixture.SyntheticBackend()
    binding = ValueBinding(seeded['organizationId'], 'synthetic-entry', 1, seeded['ownerId'], 'synthetic-operation')
    with pytest.raises(CredentialError, match='synthetic_value_required'):
        backend.put(binding, b'rejected-nonsynthetic-input')
    value = b'SYNTHETIC_FIXTURE_ONLY_49c1'
    reference = backend.put(binding, value)
    seen = []; backend.consume(reference, binding, seen.append)
    assert seen == [value]
    assert backend.status().public() == {'provider': 'synthetic-in-memory-test', 'state': 'available'}
    assert value.decode() not in json.dumps(fixture.inspect(tmp_path))
    assert all(value not in path.read_bytes() for path in tmp_path.rglob('*') if path.is_file())


def test_install_requires_explicit_isolated_server_marker(tmp_path, monkeypatch):
    monkeypatch.delenv('EIDOLON_NATIVE_CREDENTIAL_FIXTURE', raising=False)
    with pytest.raises(RuntimeError, match='Explicit synthetic server'):
        fixture.install()
    monkeypatch.setenv('EIDOLON_NATIVE_CREDENTIAL_FIXTURE', '1')
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setattr(sys, 'argv', ['eidolon', 'serve'])
    with pytest.raises((RuntimeError, FileNotFoundError)):
        fixture.install()
