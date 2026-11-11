"""Settings UI testing bar gate (AFX-55).

`scripts/check_settings_vitest.py` flags a PR that touches the Settings
surface without adding or changing a Vitest file. These cases pin its
classification and the advisory / strict exit contract, plus keep
SETTINGS_SURFACE in sync with what Settings.tsx actually renders.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
_SCRIPT = _REPO / "scripts" / "check_settings_vitest.py"

_spec = importlib.util.spec_from_file_location("check_settings_vitest", _SCRIPT)
assert _spec is not None and _spec.loader is not None
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)

_SETTINGS = "portal/web/src/pages/Settings.tsx"
_VITEST = "portal/web/tests/unit/settingsView.test.ts"


# ─────────────────────────────────────────────────────────────────────
# Surface list stays honest
# ─────────────────────────────────────────────────────────────────────


def test_every_surface_file_exists():
    missing = [p for p in gate.SETTINGS_SURFACE if not (_REPO / p).is_file()]
    assert missing == [], f"renamed/removed, update SETTINGS_SURFACE: {missing}"


def test_surface_covers_every_local_module_settings_renders():
    source = (_REPO / gate.SETTINGS_PAGE).read_text(encoding="utf-8")
    pages = _REPO / "portal/web/src/pages"
    imported = set()
    for spec in re.findall(r"from '(\.{1,2}/[^']+)'", source):
        if not spec.startswith(("../components/", "./")):
            continue
        for ext in (".tsx", ".ts"):
            candidate = (pages / f"{spec}{ext}").resolve()
            if candidate.is_file():
                imported.add(candidate.relative_to(_REPO).as_posix())
    assert imported, "expected Settings.tsx to import local components"
    assert imported <= set(gate.SETTINGS_SURFACE), sorted(
        imported - set(gate.SETTINGS_SURFACE)
    )


def test_settings_page_has_a_pinning_vitest():
    assert (_REPO / _VITEST).is_file()


# ─────────────────────────────────────────────────────────────────────
# Classification
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("surface", list(gate.SETTINGS_SURFACE))
def test_surface_change_without_vitest_is_flagged(surface):
    problem = gate.find_problem([surface, "src/agentfx/api/main.py"])
    assert problem is not None
    assert surface in problem


@pytest.mark.parametrize(
    "vitest",
    [_VITEST, "portal/web/tests/unit/poolColumnView.test.ts"],
)
def test_surface_change_with_vitest_passes(vitest):
    assert gate.find_problem([_SETTINGS, vitest]) is None


def test_nested_vitest_counts():
    assert gate.find_problem([_SETTINGS, "portal/web/tests/unit/a/b.test.ts"]) is None


def test_non_settings_change_is_ignored():
    assert gate.find_problem(["portal/web/src/pages/PaperRunDetail.tsx"]) is None
    assert gate.find_problem([]) is None


@pytest.mark.parametrize(
    "not_a_vitest",
    [
        "portal/web/tests/e2e/settings.spec.ts",
        "portal/web/tests/fixtures/indicator-values.json",
        "tests/unit/test_user_credentials.py",
    ],
)
def test_other_test_files_do_not_satisfy_the_bar(not_a_vitest):
    assert gate.find_problem([_SETTINGS, not_a_vitest]) is not None


# ─────────────────────────────────────────────────────────────────────
# Advisory vs strict
# ─────────────────────────────────────────────────────────────────────


def _fake_diff(monkeypatch, files):
    seen = {}

    def _changed(diff_range):
        seen["range"] = diff_range
        return files

    monkeypatch.setattr(gate, "changed_files", _changed)
    return seen


def test_advisory_mode_warns_but_passes(monkeypatch, capsys):
    _fake_diff(monkeypatch, [_SETTINGS])
    assert gate.main([]) == 0
    assert capsys.readouterr().out.startswith("::warning ::")


def test_strict_mode_fails(monkeypatch, capsys):
    _fake_diff(monkeypatch, [_SETTINGS])
    assert gate.main(["--strict"]) == 1
    assert capsys.readouterr().out.startswith("::error ::")


def test_clean_diff_passes_in_strict_mode(monkeypatch, capsys):
    seen = _fake_diff(monkeypatch, [_SETTINGS, _VITEST])
    assert gate.main(["--strict", "--range", "HEAD^1..HEAD"]) == 0
    assert seen["range"] == "HEAD^1..HEAD"
    assert "OK" in capsys.readouterr().out
