"""Version-truth parity (C3).

A single source of version truth — ``pyproject.toml`` ``[project].version`` —
must equal every ANCHORED current-version token the project displays. This is a
deterministic, LLM-free doc-lint that runs in the normal pytest job on every
PR/push, so version drift is caught PRE-merge (strictly better than a tag-time
release check that only fires after the drift is already live).

Surfaces asserted (one test = one surface, so the failing list is unambiguous):

    pyproject [project].version   (the canonical source — read dynamically)
      == CHANGELOG.md top released heading   `## [X.Y.Z]`
      == README.md status badge              `status-vX.Y`         (minor only)
      == README.ko.md status badge           `status-vX.Y`         (minor only)
      == STATUS.md matrix header             `vX.Y.Z (current)`
      == action.yml usage pin                `...@vX.Y.Z`
      == package ``__version__``             `X.Y.Z`

Deliberately NOT covered here (per the C3 lens):

  - Landing (``landing/index.html``) is served content — parity there is a
    release-process concern, kept out.
  - The ``git tag == pyproject`` equality belongs only in release.yml (tag-
    triggered), never on PR/main — and this repo's workflows are owner-managed.
  - HISTORICAL version references (CHANGELOG history, "wired in v0.2.0",
    STATUS's v0.0.2 column) are legitimate and must NOT be scanned — each assert
    is anchored to the one CURRENT-version token on its surface.

Until every surface is bumped to the canonical version, the un-bumped
surface(s) FAIL by design; the failure message is file:line-anchored so the
exact edit is obvious.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _search_line(text: str, pattern: re.Pattern[str]):
    """Return ``(match, line_no)`` for the first match, or ``(None, None)``."""
    m = pattern.search(text)
    if m is None:
        return None, None
    return m, text.count("\n", 0, m.start()) + 1


def _pyproject_version() -> tuple[str, int]:
    """Canonical version from ``[project].version`` (section-aware, no tomllib).

    Anchored to the ``[project]`` table so a stray ``version =`` in another
    table can never be mistaken for the canonical one.
    """
    text = _read("pyproject.toml")
    section = None
    for i, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped.strip("[]")
            continue
        if section == "project":
            m = re.match(r"""version\s*=\s*["']([^"']+)["']""", stripped)
            if m:
                return m.group(1), i
    raise AssertionError("pyproject.toml [project].version not found")


# ---------------------------------------------------------------------------
# Canonical source
# ---------------------------------------------------------------------------


def test_pyproject_version_is_semver():
    version, line = _pyproject_version()
    assert re.fullmatch(r"\d+\.\d+\.\d+", version), (
        f"pyproject.toml:{line}: version {version!r} is not X.Y.Z semver"
    )


# ---------------------------------------------------------------------------
# One assert per displayed surface (file:line-anchored messages)
# ---------------------------------------------------------------------------


def test_changelog_top_matches_pyproject():
    version, _ = _pyproject_version()
    text = _read("CHANGELOG.md")
    # First non-"Unreleased" `## [X.Y.Z]` heading = the top released version.
    m, line = _search_line(text, re.compile(r"(?m)^##\s*\[(\d+\.\d+\.\d+)\]"))
    found = m.group(1) if m else None
    assert found == version, (
        f"CHANGELOG.md:{line}: top released heading is `## [{found}]` "
        f"but pyproject version is {version}"
    )


def test_readme_badge_matches_pyproject():
    version, _ = _pyproject_version()
    major_minor = ".".join(version.split(".")[:2])
    text = _read("README.md")
    m, line = _search_line(text, re.compile(r"status-v(\d+\.\d+)(?:\.\d+)?"))
    found = m.group(1) if m else None
    assert found == major_minor, (
        f"README.md:{line}: status badge shows v{found} but pyproject "
        f"major.minor is v{major_minor} (from {version})"
    )


def test_readme_ko_badge_matches_pyproject():
    version, _ = _pyproject_version()
    major_minor = ".".join(version.split(".")[:2])
    text = _read("README.ko.md")
    m, line = _search_line(text, re.compile(r"status-v(\d+\.\d+)(?:\.\d+)?"))
    found = m.group(1) if m else None
    assert found == major_minor, (
        f"README.ko.md:{line}: status badge shows v{found} but pyproject "
        f"major.minor is v{major_minor} (from {version})"
    )


def test_status_matrix_header_matches_pyproject():
    version, _ = _pyproject_version()
    text = _read("STATUS.md")
    m, line = _search_line(text, re.compile(r"v(\d+\.\d+\.\d+)\s*\(current\)"))
    found = m.group(1) if m else None
    assert found == version, (
        f"STATUS.md:{line}: matrix header shows `v{found} (current)` "
        f"but pyproject version is {version}"
    )


def test_action_yml_pin_matches_pyproject():
    version, _ = _pyproject_version()
    text = _read("action.yml")
    m, line = _search_line(text, re.compile(r"multiagent-protocol@v(\d+\.\d+\.\d+)"))
    found = m.group(1) if m else None
    assert found == version, (
        f"action.yml:{line}: usage pin is `@v{found}` but pyproject version is {version}"
    )


def test_package_dunder_version_matches_pyproject():
    version, _ = _pyproject_version()
    text = _read("src/multiagent_protocol/__init__.py")
    match, line = _search_line(
        text,
        re.compile(r'(?m)^__version__\s*=\s*["\'](\d+\.\d+\.\d+)["\']'),
    )
    found = match.group(1) if match else None
    assert found == version, (
        "src/multiagent_protocol/__init__.py:"
        f"{line}: __version__ is {found!r} but pyproject version is {version}"
    )
