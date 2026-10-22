"""Apply config/linear/workspace.yaml to the AgentFX Linear workspace.

Idempotent: every object is matched by name and updated in place, so the
script is safe to re-run after editing the YAML. It never deletes anything
— removing a state/label/project is a deliberate manual step in Linear.

Usage:
    python scripts/linear/bootstrap.py --dry-run   # print the plan only
    python scripts/linear/bootstrap.py             # apply

API key: `LINEAR_API_KEY` env var, else ~/.config/agentfx/linear_api_key.
The key is never printed or logged.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path
from typing import Any

import yaml

API_URL = "https://api.linear.app/graphql"
REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "config" / "linear"
KEY_FILE = Path.home() / ".config" / "agentfx" / "linear_api_key"


def load_api_key() -> str:
    key = os.environ.get("LINEAR_API_KEY", "").strip()
    if not key and KEY_FILE.exists():
        key = KEY_FILE.read_text().strip()
    if not key:
        sys.exit(f"No Linear API key: set LINEAR_API_KEY or write it to {KEY_FILE}")
    return key


class Linear:
    def __init__(self, api_key: str, dry_run: bool) -> None:
        self.api_key = api_key
        self.dry_run = dry_run

    def query(self, q: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        body = json.dumps({"query": q, "variables": variables or {}}).encode()
        req = urllib.request.Request(
            API_URL,
            data=body,
            headers={"Content-Type": "application/json", "Authorization": self.api_key},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode(), strict=False)
        if payload.get("errors"):
            raise RuntimeError(json.dumps(payload["errors"], indent=2))
        return payload["data"]

    def mutate(
        self, describe: str, q: str, variables: dict[str, Any]
    ) -> dict[str, Any]:
        print(f"  {'[dry-run] ' if self.dry_run else ''}{describe}")
        if self.dry_run:
            return {}
        return self.query(q, variables)


def load_team(api: Linear, key: str) -> dict[str, Any]:
    data = api.query(
        """query { teams { nodes { id key triageEnabled cyclesEnabled issueEstimationType
             states { nodes { id name type description position } } } } }"""
    )
    for team in data["teams"]["nodes"]:
        if team["key"] == key:
            return team
    sys.exit(f"Team {key} not found — create it in Linear first")


def apply_team_settings(
    api: Linear, team: dict[str, Any], settings: dict[str, Any]
) -> None:
    print("Team settings")
    changed = {k: v for k, v in settings.items() if team.get(k) != v}
    if not changed:
        print("  up to date")
        return
    api.mutate(
        f"update {changed}",
        "mutation($id: String!, $i: TeamUpdateInput!) { teamUpdate(id: $id, input: $i) { success } }",
        {"id": team["id"], "i": changed},
    )


def apply_states(api: Linear, team_key: str, wanted: list[dict[str, Any]]) -> None:
    print("Workflow states")
    # Re-read: enabling triage creates the Triage state.
    team = load_team(api, team_key)
    existing = {s["name"]: s for s in team["states"]["nodes"]}
    for position, spec in enumerate(wanted):
        current = existing.get(spec["name"]) or existing.get(
            spec.get("rename_from", "")
        )
        if current is None:
            if spec["type"] == "triage":
                print(
                    "  Triage state appears once triageEnabled is applied (re-run after)"
                )
                continue
            api.mutate(
                f"create {spec['name']} ({spec['type']})",
                """mutation($i: WorkflowStateCreateInput!) {
                     workflowStateCreate(input: $i) { success } }""",
                {
                    "i": {
                        "teamId": team["id"],
                        "name": spec["name"],
                        "type": spec["type"],
                        "description": spec.get("description"),
                        "position": float(position),
                    }
                },
            )
            continue
        if current["type"] in ("triage", "duplicate"):
            # Linear reserves these states — they can't be renamed or edited.
            continue
        if current["type"] != spec["type"]:
            # Linear cannot change a state's category in place.
            print(
                f"  WARNING {current['name']}: type is {current['type']}, YAML says {spec['type']}"
            )
        patch = {}
        if current["name"] != spec["name"]:
            patch["name"] = spec["name"]
        if (current.get("description") or "") != (spec.get("description") or ""):
            patch["description"] = spec.get("description")
        if patch:
            api.mutate(
                f"update {current['name']} {sorted(patch)}",
                """mutation($id: String!, $i: WorkflowStateUpdateInput!) {
                     workflowStateUpdate(id: $id, input: $i) { success } }""",
                {"id": current["id"], "i": patch},
            )


def load_labels(api: Linear) -> dict[str, dict[str, Any]]:
    data = api.query(
        """query { issueLabels(first: 250) { nodes { id name description color isGroup
             team { id } parent { id name } } } }"""
    )
    # Workspace-level labels only; team labels are out of scope for this file.
    return {
        lbl["name"]: lbl for lbl in data["issueLabels"]["nodes"] if lbl["team"] is None
    }


def ensure_label(
    api: Linear,
    labels: dict[str, dict[str, Any]],
    spec: dict[str, Any],
    *,
    is_group: bool = False,
    parent: dict[str, Any] | None = None,
) -> dict[str, Any]:
    name = spec["name"]
    wanted = {
        "description": spec.get("description"),
        "color": spec.get("color") or (parent or {}).get("color"),
    }
    current = labels.get(name)
    if current is None:
        data = api.mutate(
            f"create label {'group ' if is_group else ''}{name}"
            + (f" under {parent['name']}" if parent else ""),
            """mutation($i: IssueLabelCreateInput!) {
                 issueLabelCreate(input: $i) { issueLabel { id name color } } }""",
            {
                "i": {
                    "name": name,
                    "isGroup": is_group,
                    "parentId": (parent or {}).get("id"),
                    **{k: v for k, v in wanted.items() if v},
                }
            },
        )
        created = (data.get("issueLabelCreate") or {}).get("issueLabel") or {
            "id": None,
            "name": name,
            "color": wanted["color"],
        }
        labels[name] = created
        return created
    patch = {k: v for k, v in wanted.items() if v and current.get(k) != v}
    want_parent = (parent or {}).get("id")
    if parent and (current.get("parent") or {}).get("id") != want_parent:
        patch["parentId"] = want_parent
    if patch:
        api.mutate(
            f"update label {name} {sorted(patch)}",
            """mutation($id: String!, $i: IssueLabelUpdateInput!) {
                 issueLabelUpdate(id: $id, input: $i) { success } }""",
            {"id": current["id"], "i": patch},
        )
    return current


def apply_labels(api: Linear, cfg: dict[str, Any]) -> dict[str, dict[str, Any]]:
    print("Labels")
    labels = load_labels(api)
    for group in cfg.get("label_groups", []):
        parent = ensure_label(api, labels, group, is_group=True)
        parent = {**parent, "color": group.get("color")}
        for child in group["labels"]:
            ensure_label(api, labels, child, parent=parent)
    for flag in cfg.get("labels", []):
        ensure_label(api, labels, flag)
    return labels


def apply_projects(api: Linear, team_id: str, wanted: list[dict[str, Any]]) -> None:
    print("Projects")
    data = api.query("query { projects(first: 100) { nodes { id name description } } }")
    existing = {p["name"]: p for p in data["projects"]["nodes"]}
    for spec in wanted:
        current = existing.get(spec["name"])
        if current is None:
            api.mutate(
                f"create project {spec['name']}",
                "mutation($i: ProjectCreateInput!) { projectCreate(input: $i) { success } }",
                {
                    "i": {
                        "name": spec["name"],
                        "description": spec.get("description"),
                        "teamIds": [team_id],
                    }
                },
            )
        elif (current.get("description") or "") != (spec.get("description") or ""):
            api.mutate(
                f"update project {spec['name']} description",
                "mutation($id: String!, $i: ProjectUpdateInput!) { projectUpdate(id: $id, input: $i) { success } }",
                {"id": current["id"], "i": {"description": spec.get("description")}},
            )


def apply_templates(
    api: Linear,
    team_id: str,
    wanted: list[dict[str, Any]],
    labels: dict[str, dict[str, Any]],
) -> None:
    print("Issue templates")
    data = api.query("query { templates { id name type } }")
    existing = {t["name"]: t for t in data["templates"] if t["type"] == "issue"}
    for spec in wanted:
        body = (CONFIG_DIR / "templates" / spec["file"]).read_text()
        label_ids = [
            labels[n]["id"]
            for n in spec.get("labels", [])
            if labels.get(n, {}).get("id")
        ]
        template_data = {"description": body, "labelIds": label_ids}
        current = existing.get(spec["name"])
        if current is None:
            api.mutate(
                f"create template {spec['name']}",
                "mutation($i: TemplateCreateInput!) { templateCreate(input: $i) { success } }",
                {
                    "i": {
                        "type": "issue",
                        "teamId": team_id,
                        "name": spec["name"],
                        "templateData": template_data,
                    }
                },
            )
        else:
            # Always push the body: comparing against Linear's stored
            # ProseMirror form is not worth the complexity.
            api.mutate(
                f"update template {spec['name']}",
                "mutation($id: String!, $i: TemplateUpdateInput!) { templateUpdate(id: $id, input: $i) { success } }",
                {"id": current["id"], "i": {"templateData": template_data}},
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dry-run", action="store_true", help="print the plan without changing Linear"
    )
    args = parser.parse_args()

    cfg = yaml.safe_load((CONFIG_DIR / "workspace.yaml").read_text())
    api = Linear(load_api_key(), dry_run=args.dry_run)

    team_key = cfg["team"]["key"]
    team = load_team(api, team_key)
    apply_team_settings(api, team, cfg["team"].get("settings", {}))
    apply_states(api, team_key, cfg.get("states", []))
    labels = apply_labels(api, cfg)
    apply_projects(api, team["id"], cfg.get("projects", []))
    apply_templates(api, team["id"], cfg.get("templates", []), labels)
    print("Done." if not args.dry_run else "Dry run complete — nothing changed.")


if __name__ == "__main__":
    main()
