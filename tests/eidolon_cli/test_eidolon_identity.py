"""Brand migration contracts: default personas move, user intent stays intact."""

import pytest

from eidolon_cli.config import ensure_eidolon_home
from eidolon_cli.default_soul import DEFAULT_SOUL_MD


LEGACY_DEFAULT = (
    'You are Hermes Agent, built by Nous Research. Be direct: match the length of your reply to the weight of '
    'the ask — a one-line question gets a one-line answer, and finished work gets a short report of what '
    'changed, what\'s verified, and what\'s left, never a replay of the process. No filler ("Great question," '
    '"I\'d be happy to"), no restating the request back, no re-summarizing what you already said, no narrating '
    'tool calls the user can see. Plain claims over adjectives; when unsure, say so plainly. Agree because it\'s '
    'right, not because the user said it. Depth is earned — give it when the user asks for detail, teaches, or '
    'the stakes demand it, not by default.'
)


@pytest.mark.parametrize('ascii_dash', [False, True])
def test_rebrand_upgrades_only_exact_seeded_personas(tmp_path, monkeypatch, ascii_dash):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    soul = tmp_path / 'SOUL.md'
    original = LEGACY_DEFAULT.replace('—', '--') if ascii_dash else LEGACY_DEFAULT
    soul.write_text(original, encoding='utf-8')
    ensure_eidolon_home()
    assert soul.read_text(encoding='utf-8') == DEFAULT_SOUL_MD
    assert DEFAULT_SOUL_MD.startswith('You are Eidolon, built by AetherMesh.')
    customized = original + '\nCall me my personal name and use my chosen model.'
    custom_home = tmp_path / 'customized'
    custom_home.mkdir()
    monkeypatch.setenv('HERMES_HOME', str(custom_home))
    soul = custom_home / 'SOUL.md'
    soul.write_text(customized, encoding='utf-8')
    ensure_eidolon_home()
    assert soul.read_text(encoding='utf-8') == customized


def test_default_identity_matches_seed_and_retains_runtime_skill_contract():
    from agent.prompt_builder import DEFAULT_AGENT_IDENTITY, HERMES_AGENT_HELP_GUIDANCE

    assert DEFAULT_AGENT_IDENTITY == DEFAULT_SOUL_MD
    assert 'Eidolon' in DEFAULT_AGENT_IDENTITY
    assert "skill_view(name='eidolon-agent')" in HERMES_AGENT_HELP_GUIDANCE
    assert 'https://github.com/AetherMesh-AI/Eidolon' in HERMES_AGENT_HELP_GUIDANCE
