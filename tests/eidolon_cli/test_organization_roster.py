"""Configured identities can cover only granted routes, with bounded activation."""

from dataclasses import replace

import pytest

from eidolon_cli.organization_config import OrganizationSettings, WORK_CAPABILITIES
from eidolon_cli.organization_roster import (
    configured_workers, select_staff_activation, staff_unavailability,
)


def staff(ident, *, team="general", capabilities=None, **kwargs):
    return {"id": ident, "name": ident.title(), "team": team,
            "capabilities": capabilities or ["work.draft"], **kwargs}


def settings(**raw):
    return OrganizationSettings.from_config({"organization": raw})


def test_absent_roster_is_explicitly_logical_and_empty_roster_never_falls_back():
    legacy = settings()
    assert legacy.capabilities == WORK_CAPABILITIES
    assert legacy.tool_grants == legacy.read_roots == ()
    workers = configured_workers(legacy)
    assert [worker.id for worker in workers] == [f"worker-{index}" for index in range(1, legacy.max_workers + 1)]
    assert all("Logical" in worker.name and not worker.tool_grants for worker in workers)
    assert select_staff_activation(legacy, {workers[0].id}, 2) == workers[1:]
    empty = settings(roster=[])
    assert configured_workers(empty) == ()
    with pytest.raises(ValueError, match="no generic worker"):
        select_staff_activation(empty, [], 1)


def test_route_coverage_chooses_configured_generalist_within_capacity_and_is_idempotent():
    configured = settings(roster=[
        staff("draft-specialist"),
        staff("analysis-specialist", capabilities=["work.analyze"]),
        staff("generalist", capabilities=list(WORK_CAPABILITIES)),
    ])
    routes = [("general", "work.draft"), ("general", "work.analyze")]
    selected = select_staff_activation(configured, [], 1, routes)
    assert [worker.id for worker in selected] == ["generalist"]
    assert select_staff_activation(configured, {selected[0].id}, 1, routes) == ()
    assert [worker.id for worker in select_staff_activation(configured, {selected[0].id}, 2, routes)] == ["draft-specialist"]


def test_route_and_capacity_failures_preserve_missing_disabled_and_wrong_team_reasons():
    configured = settings(roster=[
        staff("sales", team="sales"), staff("analyst", capabilities=["work.analyze"], enabled=False),
    ])
    with pytest.raises(ValueError, match="work.draft in team general"):
        select_staff_activation(configured, [], 1, [("general", "work.draft")])
    with pytest.raises(ValueError, match="disabled"):
        select_staff_activation(configured, [], 1, [("general", "work.analyze")])
    assert "missing" in staff_unavailability(None, configured)
    assert "disabled" in staff_unavailability(configured.roster[1], configured)
    assert "does not accept" in staff_unavailability(configured.roster[0], configured, "work.analyze")
    with pytest.raises(ValueError, match="capacity"):
        select_staff_activation(configured, [], configured.max_workers + 1)
    split = settings(roster=[staff("sales", team="sales"), staff("support", team="support")])
    routes = [("sales", "work.draft"), ("support", "work.draft")]
    assert [worker.id for worker in select_staff_activation(split, [], 1, routes)] == ["sales", "support"]
    assert [worker.id for worker in select_staff_activation(split, {"sales"}, 1, routes)] == ["support"]
    with pytest.raises(ValueError, match="capacity cannot cover"):
        select_staff_activation(replace(split, max_workers=1), [], 1, routes)


@pytest.mark.parametrize("missing", ["global_grant", "staff_grant", "read_roots", "capability"])
def test_inspection_requires_every_existing_grant_without_crashing_configuration(tmp_path, missing):
    raw = {"capabilities": ["work.draft", "work.inspect"], "tool_grants": ["read_file"],
           "read_roots": [str(tmp_path)], "roster": [staff("inspector", capabilities=["work.inspect"],
                                                        tool_grants=["read_file"])]}
    if missing == "global_grant":
        raw["tool_grants"] = []
        raw["roster"][0]["tool_grants"] = []
    elif missing == "staff_grant":
        raw["roster"][0]["tool_grants"] = []
    elif missing == "read_roots":
        raw["read_roots"] = []
    else:
        raw["capabilities"] = ["work.draft"]
    configured = settings(**raw)
    reason = staff_unavailability(configured.roster[0], configured, "work.inspect")
    assert reason
    with pytest.raises(ValueError, match="work.inspect in team general"):
        select_staff_activation(configured, [], 1, [("general", "work.inspect")])


def test_inspection_grants_and_provider_pair_are_preserved_without_enabling_text_tools(tmp_path):
    configured = settings(capabilities=["work.draft", "work.inspect"], tool_grants=["read_file"],
                          read_roots=[str(tmp_path)], roster=[
                              staff("reader", capabilities=["work.inspect"], tool_grants=["read_file"],
                                    provider="configured-direct-provider", model="configured-model"),
                              staff("writer"),
                          ])
    reader, writer = configured.roster
    assert reader.provider == "configured-direct-provider" and reader.model == "configured-model"
    assert reader.tool_grants == ("read_file",) and writer.tool_grants == ()
    assert staff_unavailability(reader, configured, "work.inspect") is None
    assert staff_unavailability(writer, configured, "work.draft") is None
    assert select_staff_activation(configured, [], 1, [("general", "work.inspect")]) == (reader,)
    narrowed = replace(configured, tool_grants=())
    assert "outside the current organization grant" in staff_unavailability(reader, narrowed)
    assert configured_workers(replace(configured, roster=None))[0].tool_grants == configured.tool_grants


@pytest.mark.parametrize("raw", [
    {"tool_grants": ["terminal"]}, {"tool_grants": "read_file"}, {"read_roots": ["relative/path"]},
    {"read_roots": ["/invalid\x00path"]}, {"max_tool_calls": True}, {"max_tool_calls": 0},
    {"max_tool_calls": 21}, {"max_tool_result_chars": 999}, {"max_tool_result_chars": 20001},
    {"roster": None}, {"roster": [staff("owner")]}, {"roster": [staff("reviewer-other")]},
    {"roster": [staff("a"), staff("a")]}, {"roster": [staff("../escape")]},
    {"roster": [staff("a", profile="another-profile")]},
    {"roster": [staff("a", provider="openai")]},
    {"roster": [staff("a", enabled="true")]},
    {"roster": [staff("a", tool_grants=["read_file"])]},
    {"roster": [staff("a", capabilities=["request.hire"])]},
])
def test_invalid_or_permission_widening_configuration_is_rejected(raw):
    with pytest.raises(ValueError):
        settings(**raw)
