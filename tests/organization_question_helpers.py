"""Retain pre-upgrade request behavior without rewriting its advisor assertions."""
import pytest

from eidolon_cli.organization_store import OrganizationStore


@pytest.fixture
def legacy_question_creation(monkeypatch):
    # The old writer inserted contracts without question_routes. Runtime reads,
    # reopen/migration, eligibility and completion still use production code.
    monkeypatch.setattr(OrganizationStore, '_initialize_question_route',
                        staticmethod(lambda conn, request_id: None))
