#!/usr/bin/env python3
"""Heuristic scan for personal data in source code.

Runs in CI. Refuses to merge a PR that introduces:

- Email addresses in `src/`, `tests/`, or top-level `*.py` / `*.yml` / `*.toml`
  (docs/, examples/, this script, and a few well-known headers like Apache
  license are exempt).
- Public IPv4 addresses in source (private ranges 10.0.0.0/8, 172.16.0.0/12,
  192.168.0.0/16, 127.0.0.0/8 are exempt).
- SSH-style `Host <name>` aliases in source.

This is a heuristic, not a perfect filter. Authors should still be vigilant.
Forks may extend the patterns or relax the exemptions; do not relax silently.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


# -- Paths --

INCLUDED_GLOBS = [
    "src/**/*.py",
    "tests/**/*.py",
    "*.py",
    "*.yml",
    "*.yaml",
    "*.toml",
    ".github/**/*.yml",
    ".github/**/*.yaml",
    "schemas/**/*.json",
]

EXCLUDED = {
    ".github/scripts/scan_no_personal_data.py",  # this file
}


def included_files() -> list[Path]:
    files: set[Path] = set()
    for pattern in INCLUDED_GLOBS:
        files.update(ROOT.glob(pattern))
    return sorted(p for p in files if str(p.relative_to(ROOT)) not in EXCLUDED)


# -- Patterns --

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
# IPv4: detect, then filter private/loopback ranges.
IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
SSH_HOST_RE = re.compile(r"^\s*Host\s+[A-Za-z0-9._-]+", re.MULTILINE)

# Allowlisted email patterns — placeholder forms only.
EMAIL_ALLOW = {
    "noreply@github.com",
    "noreply@anthropic.com",
    "you@example.com",
    "your.email@example.com",
    "user@example.com",
    "test@example.com",
    "<see-github-profile>",
}


def is_private_ipv4(ip: str) -> bool:
    try:
        parts = [int(x) for x in ip.split(".")]
        if len(parts) != 4 or any(p < 0 or p > 255 for p in parts):
            return True  # invalid, skip
    except ValueError:
        return True
    a, b, *_ = parts
    if a == 10:
        return True
    if a == 172 and 16 <= b <= 31:
        return True
    if a == 192 and b == 168:
        return True
    if a == 127:
        return True
    if a == 0:
        return True
    return False


def scan_file(p: Path) -> list[str]:
    findings: list[str] = []
    try:
        text = p.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return findings

    for m in EMAIL_RE.finditer(text):
        email = m.group(0).lower()
        if email in EMAIL_ALLOW:
            continue
        # example.com domain is OK
        if email.endswith("@example.com") or email.endswith("@example.org"):
            continue
        line = text.count("\n", 0, m.start()) + 1
        findings.append(f"{p.relative_to(ROOT)}:{line}: email-like literal '{email}'")

    for m in IPV4_RE.finditer(text):
        ip = m.group(0)
        if is_private_ipv4(ip):
            continue
        line = text.count("\n", 0, m.start()) + 1
        findings.append(f"{p.relative_to(ROOT)}:{line}: public IPv4 literal '{ip}'")

    for m in SSH_HOST_RE.finditer(text):
        line = text.count("\n", 0, m.start()) + 1
        findings.append(
            f"{p.relative_to(ROOT)}:{line}: SSH 'Host <alias>' literal — "
            "should be in docs/examples only"
        )

    return findings


def main() -> int:
    all_findings: list[str] = []
    for p in included_files():
        all_findings.extend(scan_file(p))

    if all_findings:
        print("::error::Personal-data heuristic flagged the following lines:")
        for f in all_findings:
            print(f"  {f}")
        print()
        print(
            "If these are intentional placeholders, move them to docs/ or examples/ "
            "(which are exempt), or add them to the EMAIL_ALLOW set in "
            ".github/scripts/scan_no_personal_data.py if they are example.com-style "
            "literals already widely-used in OSS documentation."
        )
        return 1

    print(f"scan_no_personal_data: OK (scanned {len(included_files())} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
