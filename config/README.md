# Your config (private layer)

This directory is **your private configuration** — the data half of the product.
The other half is the framework (everything else in this repo). Together:

    framework (public, shared)  +  config/ (private, yours)  =  your installation

See [`../docs/concepts/configuration-model.md`](../docs/concepts/configuration-model.md)
for the full model.

## What goes here

| File | Holds |
|------|-------|
| `owner.yml` | Your GitHub login + allowlisted reviewers |
| `projects.yml` | Governance repo + the repos the bot supervises |
| `env.yml` | Runner tier + your GitHub App slug |
| `agent_registry.yml` | Agent tools / models / machines the L4 gate trusts |
| `skills.yml` | Which built-in skills to enable/disable + severity overrides |
| `skills/` | Optional: your own validator / classifier / branch-hook plugins |

## Privacy

Everything in this directory **except this README is git-ignored** (see the
repo's `.gitignore`). That is deliberate: this is a **public** framework repo,
and your identity + repo list must never leak into it.

- **Public upstream / this framework repo:** never commit anything here. A CI
  job (`no-config-in-public`) fails the build if you do.
- **Your own deployment:** use a **private** governance repo, and commit your
  config there with `git add -f config/` (the `-f` overrides the ignore rule).
  Because the repo is private, your config stays private.

## How to create your config

- **Wizard (recommended):** open `docs/wizard/index.html` (or the hosted
  GitHub Pages version), fill the form, download the zip, unzip here.
- **By hand:** copy a starting point and edit —

      cp ../examples/solo-developer/config/*.yml ./
      # then edit owner.yml / projects.yml / env.yml

Validate against the JSON Schemas in `../schemas/` before you deploy.
