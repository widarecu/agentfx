"""Linear workspace bootstrap is additive (AFX-124).

`scripts/linear/bootstrap.py` applies `config/linear/workspace.yaml`. These
cases run `apply_states` against an in-memory team (no network, no real
Linear writes) and pin: the approved states and owners are in the YAML,
missing states are created, a second run changes nothing, and states that
still hold tickets (or are absent from the YAML) are never archived or
deleted.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

_REPO = Path(__file__).resolve().parents[3]
_SCRIPT = _REPO / "scripts" / "linear" / "bootstrap.py"
_CONFIG = _REPO / "config" / "linear" / "workspace.yaml"

_spec = importlib.util.spec_from_file_location("linear_bootstrap", _SCRIPT)
assert _spec is not None and _spec.loader is not None
bootstrap = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = bootstrap
_spec.loader.exec_module(bootstrap)


def _states() -> list[dict[str, Any]]:
    return yaml.safe_load(_CONFIG.read_text())["states"]


class FakeLinear(bootstrap.Linear):
    """Stateful stand-in for the GraphQL endpoint: applies mutations to `states`."""

    def __init__(self, states: list[dict[str, Any]]) -> None:
        super().__init__("test-key", dry_run=False)
        self.states = [dict(s) for s in states]
        self.mutations: list[str] = []

    def query(self, q: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        variables = variables or {}
        if q.lstrip().startswith("mutation"):
            self.mutations.append(q)
            if "workflowStateCreate" in q:
                spec = variables["i"]
                self.states.append(
                    {
                        "id": f"state-{len(self.states)}",
                        "name": spec["name"],
                        "type": spec["type"],
                        "description": spec.get("description"),
                        "position": spec.get("position"),
                    }
                )
            elif "workflowStateUpdate" in q:
                state = next(s for s in self.states if s["id"] == variables["id"])
                state.update(variables["i"])
            return {}
        return {
            "teams": {
                "nodes": [
                    {
                        "id": "team-afx",
                        "key": "AFX",
                        "states": {"nodes": self.states},
                    }
                ]
            }
        }


def _state(name: str, type_: str, description: str | None = None) -> dict[str, Any]:
    return {
        "id": f"id-{name}",
        "name": name,
        "type": type_,
        "description": description,
        "position": 0.0,
    }


# Today's AFX workflow, before the redesign descriptions are applied.
_LIVE = [
    _state("Triage", "triage"),
    _state("Backlog", "backlog"),
    _state("Ready", "unstarted"),
    _state("In Progress", "started"),
    _state("In Review", "started"),
    _state("In Staging", "started"),
    _state("Needs Input", "started"),
    _state("Done", "completed", "Merged."),
    _state("Canceled", "canceled"),
    _state("Duplicate", "duplicate"),
]


class TestWorkspaceYaml:
    def test_target_states_in_order(self):
        assert [s["name"] for s in _states()] == [
            "Triage",
            "Backlog",
            "Ready",
            "In Progress",
            "In Review",
            "Blocked",
            "Done",
            "Canceled",
            "Duplicate",
        ]

    @pytest.mark.parametrize(
        ("state", "owner"),
        [
            ("Backlog", "Wilson"),
            ("Ready", "Wilson"),
            ("Canceled", "Wilson"),
            ("In Progress", "afx start"),
            ("In Review", "GitHub integration"),
            ("Blocked", "afx block"),
            ("Done", "deploy sync"),
        ],
    )
    def test_each_status_names_its_owner(self, state, owner):
        spec = next(s for s in _states() if s["name"] == state)
        assert "Owner: " in spec["description"]
        assert owner in spec["description"]

    def test_done_means_verified_on_staging_not_merged(self):
        done = next(s for s in _states() if s["name"] == "Done")
        assert "staging" in done["description"]
        assert done["description"] != "Merged."

    def test_blocked_is_an_active_state(self):
        blocked = next(s for s in _states() if s["name"] == "Blocked")
        assert blocked["type"] == "started"

    @pytest.mark.parametrize("state", ["In Staging", "Needs Input"])
    def test_retired_states_are_not_listed(self, state):
        assert state not in {s["name"] for s in _states()}


class TestApplyStates:
    def test_creates_blocked_when_missing(self, capsys):
        api = FakeLinear(_LIVE)
        bootstrap.apply_states(api, "AFX", _states())
        names = [s["name"] for s in api.states]
        assert "Blocked" in names
        blocked = next(s for s in api.states if s["name"] == "Blocked")
        assert blocked["type"] == "started"

    def test_updates_existing_descriptions_in_place(self):
        api = FakeLinear(_LIVE)
        bootstrap.apply_states(api, "AFX", _states())
        done = next(s for s in api.states if s["name"] == "Done")
        assert done["id"] == "id-Done"
        assert "staging" in done["description"]

    def test_second_run_changes_nothing(self):
        api = FakeLinear(_LIVE)
        bootstrap.apply_states(api, "AFX", _states())
        api.mutations.clear()
        bootstrap.apply_states(api, "AFX", _states())
        assert api.mutations == []

    def test_states_with_tickets_survive(self):
        api = FakeLinear(_LIVE)
        bootstrap.apply_states(api, "AFX", _states())
        names = {s["name"] for s in api.states}
        assert {"In Staging", "Needs Input"} <= names

    def test_states_absent_from_yaml_are_left_alone(self):
        legacy = _state("QA Hold", "started", "old")
        api = FakeLinear([*_LIVE, legacy])
        bootstrap.apply_states(api, "AFX", _states())
        assert legacy in api.states
        assert all("QA Hold" not in json.dumps(m) for m in api.mutations)

    def test_never_archives_or_deletes(self):
        api = FakeLinear(_LIVE)
        bootstrap.apply_states(api, "AFX", _states())
        assert api.mutations
        for mutation in api.mutations:
            assert "Archive" not in mutation and "Delete" not in mutation

    def test_dry_run_writes_nothing(self):
        api = FakeLinear(_LIVE)
        api.dry_run = True
        bootstrap.apply_states(api, "AFX", _states())
        assert api.mutations == []

    @pytest.mark.parametrize(
        "mutation",
        [
            "mutation($id: String!) { workflowStateArchive(id: $id) { success } }",
            "mutation($id: String!) { issueLabelDelete(id: $id) { success } }",
        ],
    )
    def test_archive_or_delete_mutation_is_refused(self, mutation):
        api = FakeLinear(_LIVE)
        with pytest.raises(RuntimeError, match="never archives or deletes"):
            api.mutate("remove something", mutation, {"id": "x"})
        assert api.mutations == []
