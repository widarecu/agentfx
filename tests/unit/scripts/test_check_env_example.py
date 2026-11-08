"""`.env.example` <-> config.py contract (AFX-80).

`scripts/check_env_example.py` is the enforcement. Running it from the unit
suite is what wires it into CI: ci.yml and agent-fleet-ci.yml both run
`pytest tests/unit -m unit`, so a PR that adds a config.py setting without
mirroring it into `.env.example` fails in that PR, not six months later.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from agentfx.api.core.config import Settings

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check_env_example.py"

# Loaded by path: `scripts/` is not a package, and a bare `import scripts...`
# could resolve to an unrelated top-level `scripts` package in site-packages.
_spec = importlib.util.spec_from_file_location("check_env_example", _SCRIPT)
assert _spec is not None and _spec.loader is not None
check_env_example = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_env_example)

find_drift = check_env_example.find_drift

_CONFIG = """
from pydantic import Field

class Settings:
    '''Docstring mentioning env="GHOST_IN_DOCSTRING" must not count.'''

    # env="GHOST_IN_COMMENT" must not count either.
    FOO: str = Field(default="", env="FOO")
    BAR: int = Field(default=1, env="BAR")
    INGEST: bool = Field(default=True, env="INGEST")
"""
_GOVERNED = frozenset({"INGEST"})


# ─────────────────────────────────────────────────────────────────────
# The real files: the actual contract
# ─────────────────────────────────────────────────────────────────────


def test_env_example_documents_every_config_setting():
    problems = find_drift(
        check_env_example.CONFIG_PY.read_text(encoding="utf-8"),
        check_env_example.ENV_EXAMPLE.read_text(encoding="utf-8"),
    )
    assert problems == [], "\n".join(problems)


def test_env_example_loads_as_safe_settings(monkeypatch):
    """`make dotenv` copies the template verbatim into `.env.development`, so
    every active line must validate (an empty int or bool would crash a fresh
    checkout at startup), and the copy must not switch anything on."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name, raising=False)

    settings = Settings(_env_file=check_env_example.ENV_EXAMPLE)

    # Dark-launch flags ship off.
    assert settings.AGENT_FLEET_ENABLED is False
    assert settings.AGENT_PE_WORKSPACE_ENABLED is False
    assert settings.AGENT_LINEAR_ENABLED is False
    assert settings.LINEAR_AGENT_ENABLED is False
    # Secrets ship empty: the template must never carry a real credential.
    for secret in (
        "FERNET_KEY",
        "SMTP_PASSWORD",
        "GITHUB_TOKEN",
        "LINEAR_API_KEY",
        "LINEAR_AGENT_WEBHOOK_SECRET",
        "LINEAR_AGENT_CLIENT_SECRET",
    ):
        assert getattr(settings, secret) == "", f"{secret} must be empty"


# ─────────────────────────────────────────────────────────────────────
# Checker semantics, on synthetic inputs
# ─────────────────────────────────────────────────────────────────────


def test_in_sync_template_has_no_problems():
    env = "FOO=\nBAR=1\n# INGEST is set per surface, never here.\n"
    assert find_drift(_CONFIG, env, _GOVERNED) == []


def test_missing_setting_is_reported_by_name():
    problems = find_drift(_CONFIG, "FOO=\n# INGEST lives elsewhere\n", _GOVERNED)
    assert len(problems) == 1
    assert problems[0].startswith("BAR:")


def test_commented_assignment_counts_as_an_entry():
    env = "FOO=\n# BAR=1\n# INGEST lives elsewhere\n"
    assert find_drift(_CONFIG, env, _GOVERNED) == []


def test_prose_mention_is_not_an_entry():
    env = "FOO=\n# BAR controls something, but nothing assigns it\n# INGEST\n"
    assert [p.split(":")[0] for p in find_drift(_CONFIG, env, _GOVERNED)] == ["BAR"]


def test_env_keyword_only_counts_in_real_calls():
    assert check_env_example.declared_env_names(_CONFIG) == {"FOO", "BAR", "INGEST"}


@pytest.mark.parametrize("line", ["INGEST=true", "export INGEST=true"])
def test_surface_governed_setting_must_not_be_assigned(line):
    problems = find_drift(_CONFIG, f"FOO=\nBAR=1\n{line}\n", _GOVERNED)
    assert len(problems) == 1
    assert problems[0].startswith("INGEST:")
    assert "must not be assigned" in problems[0]


def test_surface_governed_setting_may_appear_in_commented_examples():
    env = "FOO=\nBAR=1\n#   INGEST=false make docker-up PROFILE=test\n"
    assert find_drift(_CONFIG, env, _GOVERNED) == []


def test_surface_governed_setting_must_still_be_mentioned():
    problems = find_drift(_CONFIG, "FOO=\nBAR=1\n", _GOVERNED)
    assert problems == ["INGEST: surface-governed but never mentioned"]


def test_stale_surface_governed_exemption_is_reported():
    env = "FOO=\nBAR=1\n# INGEST\n"
    problems = find_drift(_CONFIG, env, _GOVERNED | {"RENAMED_AWAY"})
    assert len(problems) == 1
    assert problems[0].startswith("RENAMED_AWAY:")


def test_main_exit_status_and_report(tmp_path, monkeypatch, capsys):
    config = tmp_path / "config.py"
    env_example = tmp_path / ".env.example"
    config.write_text('X = Field(env="ONLY_SETTING")\n', encoding="utf-8")
    monkeypatch.setattr(check_env_example, "CONFIG_PY", config)
    monkeypatch.setattr(check_env_example, "ENV_EXAMPLE", env_example)
    monkeypatch.setattr(check_env_example, "SURFACE_GOVERNED", frozenset())

    env_example.write_text("# nothing here yet\n", encoding="utf-8")
    assert check_env_example.main() == 1
    assert "ONLY_SETTING" in capsys.readouterr().err

    env_example.write_text("ONLY_SETTING=\n", encoding="utf-8")
    assert check_env_example.main() == 0
    assert "OK" in capsys.readouterr().out
