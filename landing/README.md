# landing/ — the public explainer page

(Named `landing/`, not `site/`, because `site/` is git-ignored as a docs
build-output convention.)

`index.html` is a fully self-contained, bilingual (ko default / en toggle)
explainer for non-developer operators. Since 2026-10-06 it describes the
operating discipline as the author actually runs it — presented as a worked
example with no personal data (no names, contacts or hosts); adopters keep their
own values in their own private config — with this engine as the option for
private repositories on GitHub Free:

- why an AI team needs discipline;
- the team shape — a command seat (the manager: plans, assigns, designs the
  checks, declares done), a Codex labor seat (the builder, with an explicit
  effort per job), an Opus workhorse for non-code volume work, a checker that
  never made the work (preferably another vendor), and a cross-vendor plan
  consult for heavy plans — plus how one piece of work flows and the
  allocation rules;
- the four devices (traffic light / approvals / name tags / receipts) as they
  run today: GitHub Pro rulesets, two questions plus a written severe list
  with standing delegation, six identity trailers, receipt-backed done
  verdicts;
- the ten-article quality constitution and the working habits;
- the preference layer, a delegated setup prompt for the current setup, and an
  honest machine-enforced vs discipline-kept status;
- a collapsed appendix for private repositories on GitHub Free: this engine's
  delegated install path (wizard / bootstrap prompt / runbook), its status, and
  how it relates to similar tools.

Model names on the page are the current seat occupants. When a generation
changes, update the team cards and the date stamps (hero kicker, footer).

- **No build step.** One static file; the only external request is the SUIT
  variable font from jsDelivr.
- **No analytics, no telemetry** — same rule as the bot
  (`AGENTS.md` non-negotiable #6).
- **No personal data** — the page carries only the public repository links.
  `.github/scripts/scan_no_personal_data.py` does not currently include
  `landing/**` in its `INCLUDED_GLOBS`, so check the page with a locally
  widened copy of the scan (or by hand) before each deploy.

Serve it from anything that can serve a static file (GitHub Pages, any
reverse proxy, `python -m http.server`). Content honesty rule: the engine
appendix's "works / not yet" lists mirror [`STATUS.md`](../STATUS.md) — update
both in the same PR when shipping engine behavior changes. The operating-model
sections follow the author's current practice and carry a last-updated date.

Canonical deployment (2026-07-07): served at **`https://ai.jdg.dev/multiagent/`**
— that domain hosts multiple content sections, so the root redirects here
until a hub index exists. Redeploy = copy this file into the host's
`site/multiagent/` directory (read-only static mount; no restart needed).
