"""Workflow `run:` steps reach bash intact (AFX-131).

An unquoted ` #` in a one-line `run:` is a YAML comment, so the step gets a
truncated script: `run: echo "PR #$N"` runs `echo "PR`. Pins that no plain
`run:` scalar is cut short by a comment and that every bash step parses
with `bash -n`.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

_WORKFLOWS = Path(__file__).resolve().parents[3] / ".github" / "workflows"
_FILES = sorted(_WORKFLOWS.glob("*.yml"))
_EXPRESSION = re.compile(r"\$\{\{.*?\}\}", re.DOTALL)


def _run_nodes(node: yaml.Node):
    if isinstance(node, yaml.MappingNode):
        for key, value in node.value:
            if key.value == "run" and isinstance(value, yaml.ScalarNode):
                yield value
            yield from _run_nodes(value)
    elif isinstance(node, yaml.SequenceNode):
        for item in node.value:
            yield from _run_nodes(item)


def _bash_steps(workflow: dict):
    for job_id, job in (workflow.get("jobs") or {}).items():
        default = (job.get("defaults") or {}).get("run", {}).get("shell", "bash")
        for i, step in enumerate(job.get("steps") or []):
            if "run" in step and step.get("shell", default).startswith("bash"):
                yield f"{job_id}[{i}] {step.get('name', '')}", step["run"]


def test_workflows_found():
    assert any(f.name == "pr-review-relay.yml" for f in _FILES)


@pytest.mark.parametrize("path", _FILES, ids=lambda p: p.name)
def test_no_plain_run_scalar_is_cut_short_by_a_comment(path):
    lines = path.read_text().splitlines()
    cut = []
    for node in _run_nodes(yaml.compose(path.read_text())):
        if node.style is not None:
            continue
        rest = lines[node.end_mark.line][node.end_mark.column :]
        if rest.strip().startswith("#"):
            cut.append(f"line {node.end_mark.line + 1}: {node.value!r}")
    assert not cut, f"`run:` truncated by a YAML comment (use `run: |`): {cut}"


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not installed")
@pytest.mark.parametrize("path", _FILES, ids=lambda p: p.name)
def test_every_bash_step_parses(path):
    bad = []
    for where, script in _bash_steps(yaml.safe_load(path.read_text())):
        result = subprocess.run(
            ["bash", "-n"],
            input=_EXPRESSION.sub("X", script),
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            bad.append(f"{where}: {result.stderr.strip()}")
    assert not bad, bad


def test_the_relay_regression_is_caught(tmp_path):
    wf = tmp_path / "bad.yml"
    wf.write_text(
        "jobs:\n  j:\n    steps:\n"
        '      - run: echo "Review on PR #$PR_NUMBER; re-runs."\n'
    )
    node = next(_run_nodes(yaml.compose(wf.read_text())))
    assert node.value == 'echo "Review on PR'
    rest = wf.read_text().splitlines()[node.end_mark.line][node.end_mark.column :]
    assert rest.strip().startswith("#")
    result = subprocess.run(
        ["bash", "-n"], input=node.value, capture_output=True, text=True, check=False
    )
    assert result.returncode != 0
