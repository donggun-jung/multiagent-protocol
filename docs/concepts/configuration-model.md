# Configuration model: framework + your config

`multiagent-protocol` is built so that **one codebase serves everyone**. There
is no "public version" and "my private version" of the *code*. There is one
framework (this repository) plus a small, private **config layer** that differs
per operator. Your installation is the sum of the two:

    framework (public, shared, generic)
      +  your config (private, yours)
      =  your running installation

This page explains the split, why it exists, and how to keep your half private.

## The two layers

| | Framework | Your config |
|---|---|---|
| **What** | Bot code, doctrine, schemas, built-in skills, web wizard | Identity, repo list, agent registry, skill toggles, custom skills |
| **Where** | Everything tracked in this repo *except* `config/` | `config/` (git-ignored here) |
| **Audience** | Everyone — shared upstream | Only you |
| **Personal data?** | None, ever (CI-enforced) | Yes — your login, your repos |
| **Changes** | When the protocol improves | When your setup changes |
| **Updated by** | Sync from upstream | You (wizard or by hand) |

The framework ships **opinionated defaults** ("general preferences" — e.g. no
hallucinated file references, trailers required). Those live in the framework as
built-in skills, not in your config, because they apply to everyone. You *tune*
them in `config/skills.yml`; you do not re-state them. See
[`general-preferences.md`](general-preferences.md).

## Your config: five files + optional skills

All under `config/` (see [`../../config/README.md`](../../config/README.md)):

- **`owner.yml`** — your GitHub login + any allowlisted reviewers. Personal.
- **`projects.yml`** — governance repo, supervised repos, Decision Inbox host,
  break-glass deadline. Names your real repositories → personal.
- **`env.yml`** — runner tier, your GitHub App slug. Identifies your App.
- **`agent_registry.yml`** — which agent tools / models / machines the L4
  identity gate trusts. May name your machines → personal.
- **`skills.yml`** — enable/disable built-ins, severity overrides. Preference.
- **`config/skills/`** — optional: your own validator / classifier / branch-hook
  plugins. Loaded by `src/multiagent_protocol/skills/loader.py`.

Each file validates against a schema in [`../../schemas/`](../../schemas/).

## Why the split

1. **No personal data in public.** The framework is public for branding and
   reuse. Your login and repo topology are not. Separating them lets the public
   repo be audited to contain zero personal data — and it is, on every CI run.
2. **No code fork.** If your settings lived in code, you would maintain a fork
   that drifts from upstream. As *data*, your config rides on top of an
   unmodified framework; you take updates by syncing, never by merging code.
3. **Reuse by strangers.** Someone else's installation differs from yours only
   in `config/`. The onboarding wizard exists precisely to generate that one
   directory.

## Keeping your config private

`config/` is git-ignored in this framework repo (only `config/README.md` is
tracked). Two rules:

- **This public repo:** never commit anything under `config/`. The CI job
  `no-config-in-public` (in `.github/workflows/tests.yml`) fails the build if a
  public repo tracks any `config/` file other than the README. It is skipped
  automatically when the repository is private.
- **Your deployment:** the bot reads `config/` at runtime from the repo its
  workflow checks out, so your **governance repo must contain your config**.
  Make that repo **private**, then commit config with `git add -f config/`
  (the `-f` overrides the ignore rule). Private repo → private config.

### Deployment shapes

| Shape | How config is supplied | Good for |
|---|---|---|
| **Private fork** (simplest) | Fork this repo *private*, force-add `config/` | Most solo devs |
| **Separate private config repo** | Framework stays upstream; a private repo holds only `config/`, checked out in CI | Keeping framework + config in separate histories |
| **Actions secrets** | Inject individual values as secrets/vars at runtime | Minimal-footprint, no committed config |

The protocol does not force one shape. It only requires that, at bot runtime, a
valid `config/` is present in the working directory (`load_config()` in
`src/multiagent_protocol/config/loader.py`).

## Creating your config

- **Wizard (recommended):** [`../wizard/index.html`](../wizard/index.html) — a
  static, no-backend form. Fill it in, download the zip, unzip into `config/`.
  This is the "onboarding + personal-settings" feature that ships with the
  public framework.
- **By hand:** `cp examples/solo-developer/config/*.yml config/` and edit.

Either way: validate against [`../../schemas/`](../../schemas/), keep the result
in a **private** repo, and never paste it into a public one.

## Summary

The framework is shared and public; your config is yours and private; the wizard
bridges the two for newcomers. That is the whole model — and why there is no
"public build" separate from "your build."
