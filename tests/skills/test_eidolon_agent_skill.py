"""The Eidolon self-help skill routes to shipped references and current docs.

No public documentation deployment is assumed. A source checkout can generate
an index; a configured documentation origin must flow into that generated index.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SKILL_DIR = REPO / "skills" / "autonomous-ai-agents" / "eidolon-agent"
SKILL_MD = SKILL_DIR / "SKILL.md"
GENERATOR = REPO / "website" / "scripts" / "generate-llms-txt.py"


@pytest.fixture(scope="module")
def skill_text() -> str:
    return SKILL_MD.read_text(encoding="utf-8")


def test_every_referenced_file_exists(skill_text):
    """Routing a question to a file that isn't there is a dead end."""
    targets = set(re.findall(r"`((?:references|templates)/[^`]+)`", skill_text))

    assert targets, "the skill's routing table no longer references any files"
    for target in sorted(targets):
        assert (SKILL_DIR / target).exists(), f"SKILL.md routes to missing {target}"


def test_every_reference_is_reachable_from_the_skill(skill_text):
    """An unrouted reference is one the agent will never think to open.

    This is the failure that produced the original complaint: content can exist
    and still be invisible because nothing points at it.
    """
    on_disk = {f"references/{path.name}" for path in (SKILL_DIR / "references").glob("*.md")}
    routed = set(re.findall(r"`(references/[^`]+)`", skill_text))

    assert not (on_disk - routed), (
        f"reference files no reader will ever reach: {sorted(on_disk - routed)} — "
        "add a routing-table row in SKILL.md"
    )


def test_unknown_features_route_to_current_docs_and_optional_index(skill_text):
    """The catch-all is what makes coverage of the whole product possible."""
    assert "/docs/llms.txt" in skill_text
    # web_extract can be disabled; terminal never is.
    assert "curl" in skill_text, "no way to reach the index without web tools"


def test_configured_docs_origin_is_used_by_the_generated_index(skill_text, monkeypatch, tmp_path):
    """The optional docs host and the generated index must agree."""
    monkeypatch.setenv("EIDOLON_DOCS_URL", "https://docs.example.test")
    spec = importlib.util.spec_from_file_location("generate_llms_txt", GENERATOR)
    assert spec is not None and spec.loader is not None
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)

    monkeypatch.setattr(gen, "STATIC", tmp_path)
    gen.main()
    index = (tmp_path / "llms.txt").read_text(encoding="utf-8")
    assert f"{gen.SITE_BASE}/getting-started/quickstart" in index
    assert "EIDOLON_DOCS_URL" in skill_text
    assert "website/static/llms.txt" in skill_text
    assert "https://hermes-agent.nousresearch.com/docs/" not in skill_text
