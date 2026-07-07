# landing/ — the product landing page

(Named `landing/`, not `site/`, because `site/` is git-ignored as a docs
build-output convention.)

`index.html` is a fully self-contained, bilingual (ko default / en toggle)
landing page for non-developer operators: why you need merge discipline for
an AI team, the four devices (traffic light / approval inbox / name tags /
receipts) explained without jargon, the preference layer, and the delegated
install path.

- **No build step.** One static file; the only external request is the SUIT
  variable font from jsDelivr.
- **No analytics, no telemetry** — same rule as the bot
  (`AGENTS.md` non-negotiable #6).
- **No personal data** — covered by `.github/scripts/scan_no_personal_data.py`
  (the `landing/**` globs are in its INCLUDED_GLOBS).

Serve it from anything that can serve a static file (GitHub Pages, any
reverse proxy, `python -m http.server`). Content honesty rule: the
"what works / what doesn't yet" section mirrors [`STATUS.md`](../STATUS.md) —
update both in the same PR when shipping behavior changes.

Canonical deployment (2026-07-07): served at **`https://ai.jdg.dev/multiagent/`**
— that domain hosts multiple content sections, so the root redirects here
until a hub index exists. Redeploy = copy this file into the host's
`site/multiagent/` directory (read-only static mount; no restart needed).
