# Security Policy

## Threat model

The `multiagent-protocol` bot holds **merge permission** on the `main` branch of every repository it is installed on. A vulnerability in the bot is therefore a vulnerability in the integrity of that branch. We take security seriously.

Specifically, the following are in-scope security issues:

1. **Authentication bypass** — anything that allows code or commits to merge without the L1 5-condition gate.
2. **Classifier impersonation** — anything that allows an attacker to publish a `classifier-judgment` check-run that the bot honors (the publisher-identity gate must hold; see `src/multiagent_protocol/skills/builtin/validator_classifier_publisher.py`).
3. **Trailer forgery / identity confusion** — anything that allows a commit to pass the L4 identity gate while misrepresenting which agent/session authored it.
4. **Break-glass bypass** — anything that allows a `[break-glass-*]` commit to land on `main` without triggering L5 audit + ADR-within-24h requirement.
5. **Decision Inbox manipulation** — anything that allows non-owner reactions/comments to count as "owner approval" on Quadrant D issues.
6. **Secret leakage** — accidental logging or commit of PEM private keys, GitHub App tokens, or owner credentials.
7. **Mirror cascade trust violation** — anything that allows an adopter repository to forge a successful sync against `canonical_paths` without actually matching content.
8. **Web wizard XSS / code execution** — the wizard is a static page that processes user input; any path that lets attacker-controlled input run as JavaScript in another user's wizard session is in scope.
9. **Version-truth false green** — anything that lets a completion subreceipt
   pass against a mutable or substituted Git object, stale product `main`, an
   alternate state path, an invalid release identifier, or an active Git
   replacement ref.

The following are **out of scope**:

- Compromised GitHub account of the bot operator (this is your own machine to defend).
- Compromised VPS / self-hosted runner host (this is your own infrastructure).
- Brute-force or social engineering of GitHub itself.
- Loss of bot maintenance funding / availability (this project is best-effort; see `MAINTAINERS.md`).
- Live deployment-instance causality, endpoint readback, and build-artifact
  provenance. The declared-state completion profile lists these as unverified
  and never authorizes completion; product-specific deployment attestations
  must supply them.

## Reporting a vulnerability

**Do not open a public GitHub Issue.** Use one of the following channels:

1. **GitHub Security Advisories**: <https://github.com/donggun-jung/multiagent-protocol/security/advisories/new>
   Preferred channel. Encrypted in transit, viewable only by maintainers.

2. **Email**: send to the address listed in `MAINTAINERS.md` with `[SECURITY]` in the subject. If you require PGP, request a key in the first message.

In your report, please include:

- A description of the vulnerability.
- Steps to reproduce (ideally a minimal proof-of-concept).
- The version (commit SHA or release tag) you tested against.
- Your assessment of severity (CVSS or Low/Medium/High/Critical).
- Whether you intend to publish the finding, and if so, your preferred disclosure timeline.

## Our response

- **Initial acknowledgement**: within **7 days** of your report.
- **Severity assessment**: within **14 days**.
- **Patch development**: depends on severity, but we aim for:
  - Critical (auth bypass, RCE, key exfiltration): within **14 days**.
  - High: within **30 days**.
  - Medium / Low: best-effort, no fixed window.
- **Public disclosure**: coordinated with you. Default is **90 days** from initial report, but we will negotiate shorter for actively-exploited issues or longer for complex fixes.

We do not have a bug bounty program. We will credit you in the advisory and CHANGELOG unless you prefer otherwise.

## Hardening you can do as an operator

Even with a perfect bot, the operator must hold up their end:

- **Run on the GitHub Free tier branch protection limitation, not in spite of it.** If you can pay for GitHub Pro, GitHub's built-in branch protection is auditable and battle-tested by GitHub's security team. This project is for people who cannot or will not pay; you accept the tradeoffs.
- **Rotate the GitHub App private key on a 90-day cadence, and immediately on any suspected leak.** GitHub Apps support multiple active private keys; generate the new key first, deploy it to Actions secrets, then delete the old key. If the key ever appears in a log, public repo, or chat transcript, rotate immediately and audit `bot-state/classifier_audit.jsonl` for the window in which the leak might have been usable.
- **Use fine-grained Personal Access Tokens** for any self-hosted runner credentials, scoped to the minimum repos and permissions needed.
- **Review `Agent-Session` IDs.** A session ID that does not match the regex
  `^s_[a-z0-9][a-z0-9-]{2,14}[a-z0-9]$` is suspicious; the L4 identity gate
  should already reject it, but watch the audit log.
- **Read your own Decision Inbox.** The bot routes irreversible-and-critical actions to you for a reason. Do not blanket-approve.
- **Protect the registry-origin trust anchor.** Supply the expected canonical
  slug from protected automation configuration. A checker cannot discover the
  intended governance remote if an attacker controls both that input and the
  command invocation.

## Supported versions

| Version | Supported     |
|---------|---------------|
| 1.x     | Yes — current |
| < 1.0   | No            |

`1.0.0` is the first stable release: the cron orchestrator and the L1–L5 enforcers ship working, after multiple rounds of independent external review. We aim to keep API/schema/CLI compatibility within the `1.x` line.
