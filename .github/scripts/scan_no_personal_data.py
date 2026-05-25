#!/usr/bin/env python3
"""Heuristic scan for personal data and secrets in source code.

Runs in CI. Refuses to merge a PR that introduces, in `src/`, `tests/`,
or top-level `*.py` / `*.yml` / `*.toml` / `.github/**/*.yml`:

- Email addresses (docs/, examples/, this script, and `example.com`-style
  literals are exempt).
- Public IPv4 addresses (private + loopback + RFC 1918 ranges are exempt).
- SSH-style ``Host <name>`` aliases.
- Cloud / vendor secrets prefixes that should NEVER appear in source:
    * AWS access keys (``AKIA[0-9A-Z]{16}`` and ``ASIA[0-9A-Z]{16}``)
    * GCP service-account JSON private-key fragments
      (``-----BEGIN PRIVATE KEY-----`` outside a *.pem file in tests/)
    * GitHub personal access tokens (``ghp_``, ``ghs_``, ``gho_``,
      ``ghu_``, ``ghr_`` prefixes followed by 36 chars)
    * Slack tokens (``xox[abp]-``)

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

# Cloud / vendor secrets — these prefixes should never appear in source.
# Each pattern is anchored to a recognizable prefix so we do not false-
# positive on arbitrary base64 strings.
SECRET_PATTERNS = [
    # AWS access key IDs.
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS access key (AKIA...)"),
    (re.compile(r"\bASIA[0-9A-Z]{16}\b"), "AWS temporary access key (ASIA...)"),
    # GitHub personal access tokens (multiple prefix variants).
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"), "GitHub PAT (ghp_/ghs_/gho_/ghu_/ghr_ prefix)"),
    # Slack tokens.
    (re.compile(r"\bxox[abp]-[A-Za-z0-9-]{10,}\b"), "Slack token (xox[abp]- prefix)"),
    # Google API key (AIza prefix).
    (re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), "Google API key (AIza prefix)"),
]

# A PRIVATE KEY block in source code outside docs/ + examples/ is almost
# always a leak. The pattern matches the standard PEM/OpenSSH headers.
PEM_HEADER_RE = re.compile(
    r"-----BEGIN (?:RSA |OPENSSH |EC |DSA |ENCRYPTED |)PRIVATE KEY-----"
)

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

    for pat, label in SECRET_PATTERNS:
        for m in pat.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            findings.append(
                f"{p.relative_to(ROOT)}:{line}: looks like a {label}. "
                f"Never commit credentials, even revoked ones."
            )

    for m in PEM_HEADER_RE.finditer(text):
        line = text.count("\n", 0, m.start()) + 1
        findings.append(
            f"{p.relative_to(ROOT)}:{line}: PEM PRIVATE KEY header in "
            f"source. Move to a .pem file under .gitignore or a secret "
            f"manager."
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
