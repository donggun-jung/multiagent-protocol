// multiagent-protocol setup wizard — pure-client config generator.
// No network calls. Reads form, builds YAML strings, offers download + App Manifest URL.

// -----------------------------------------------------------------------------
// Language switching
// -----------------------------------------------------------------------------

// Currently-selected UI language. Used by dynamic messages (e.g. the
// vocabulary warning) that are built in JS rather than by [data-i18n].
let currentLang = "en";

// Look up a localized string, falling back to English then the key itself.
function t(key) {
  const en = LOCALES.en || {};
  const cur = LOCALES[currentLang] || en;
  if (cur[key] != null) return cur[key];
  if (en[key] != null) return en[key];
  return key;
}

document.querySelectorAll(".lang-switch button").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".lang-switch button").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    currentLang = btn.getAttribute("data-lang");
    applyLocale(currentLang);
    // Dynamic strings are not covered by applyLocale; refresh them.
    refreshVocabWarning();
  });
});

// Default locale on load.
applyLocale("en");

// -----------------------------------------------------------------------------
// Config generation
// -----------------------------------------------------------------------------

function lines(textareaValue) {
  return textareaValue
    .split("\n")
    .map(s => s.trim())
    .filter(s => s.length > 0);
}

function yamlList(items, indent = 2) {
  if (items.length === 0) return " []";
  const pad = " ".repeat(indent);
  return "\n" + items.map(s => pad + "- " + s).join("\n");
}

function generateOwnerYml(state) {
  const lines = [];
  lines.push(`github_login: ${state.ownerLogin}`);
  const all = [state.ownerLogin, ...state.allowlistedExtra];
  if (state.allowlistedExtra.length > 0) {
    lines.push(`allowlisted_actors:`);
    all.forEach(a => lines.push(`  - ${a}`));
  } else {
    lines.push(`# allowlisted_actors defaults to [github_login].`);
    lines.push(`# To delegate approval rights, uncomment and edit:`);
    lines.push(`# allowlisted_actors:`);
    lines.push(`#   - ${state.ownerLogin}`);
  }
  if (state.displayName) {
    lines.push(`display_name: ${state.displayName}`);
  }
  return lines.join("\n") + "\n";
}

function generateProjectsYml(state) {
  const lines = [];
  lines.push(`governance_repo: ${state.governanceRepo}`);
  lines.push(`supervised_repos:`);
  if (state.supervisedRepos.length === 0) {
    lines.push(`  []  # add repos to gate here`);
  } else {
    state.supervisedRepos.forEach(r => lines.push(`  - ${r}`));
  }
  if (state.botRepo) {
    lines.push(`bot_repo: ${state.botRepo}`);
  }
  return lines.join("\n") + "\n";
}

function generateEnvYml(state) {
  const lines = [];
  lines.push(`runner_tier: ${state.runnerTier}`);
  lines.push(`classifier_publisher_slug: github-actions`);
  lines.push(`bot_app_slug: ${state.botAppSlug || "your-merge-gate"}`);
  return lines.join("\n") + "\n";
}

function generateSkillsYml(state) {
  const disabled = [];
  if (!state.skillHallucination) disabled.push("hook_hallucination_guard");
  if (!state.skillEmptyPr) disabled.push("classifier_empty_pr");

  const lines = [];
  lines.push(`enabled: []`);
  if (disabled.length === 0) {
    lines.push(`disabled: []`);
  } else {
    lines.push(`disabled:`);
    disabled.forEach(s => lines.push(`  - ${s}`));
  }
  lines.push(`severity_overrides: {}`);
  return lines.join("\n") + "\n";
}

function generateAgentRegistryYml(state) {
  const lines = [];
  lines.push(`tools:`);
  state.agents.forEach(a => lines.push(`  - ${a}`));
  lines.push(`models:`);
  state.agents.forEach(a => {
    if (a === "manual" || a === "github-actions") {
      lines.push(`  ${a}: ["n/a"]`);
    } else {
      lines.push(`  ${a}: ["*"]`);
    }
  });
  lines.push(`machines: []`);
  return lines.join("\n") + "\n";
}

// Today's date as YYYY-MM-DD in the operator's local timezone. Used to date
// taste-ledger entries. Local (not UTC) so the date matches the day the
// operator is actually filling the form.
function todayISO() {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

// Quote a value as a YAML double-quoted scalar so free text (colons, '#',
// quotes, leading digits) survives round-trip. Always quote — simpler and
// always safe for the short strings we emit here.
function yamlQuote(s) {
  return '"' + String(s).replace(/\\/g, "\\\\").replace(/"/g, '\\"') + '"';
}

// Render config/preferences.yml from the preferences sub-state. Optional
// sections with no content are omitted entirely (no empty keys), per the
// preferences schema and the "omit empty" wizard rule. `language` (with a
// primary) is the only required block.
function generatePreferencesYml(p) {
  const out = [];
  out.push(`# Operator working preferences — the personal layer of this installation.`);
  out.push(`# Read by YOUR agents (installed into supervised repos via templates/adopter).`);
  out.push(`# The bot never reads this file. Schema: schemas/preferences.schema.json`);
  out.push(``);

  // language (required: primary)
  out.push(`language:`);
  out.push(`  primary: ${yamlQuote(p.primaryLang)}`);
  out.push(`  reports: ${p.reports}`);

  // communication (all fields have defaults; emit them so the file is explicit)
  out.push(`communication:`);
  out.push(`  report_style: ${p.reportStyle}`);
  out.push(`  decision_format: ${p.decisionFormat}`);
  out.push(`  batch_questions: ${p.batchQuestions ? "true" : "false"}`);

  // autonomy: profile always; quiet_hours + timezone only when set.
  out.push(`autonomy:`);
  out.push(`  profile: ${p.autonomyProfile}`);
  if (p.quietHours) {
    out.push(`  quiet_hours: ${yamlQuote(p.quietHours)}`);
  }
  if (p.timezone) {
    out.push(`  timezone: ${yamlQuote(p.timezone)}`);
  }

  // taste_ledger: only if the operator seeded rules.
  if (p.tasteLedger.length > 0) {
    const today = todayISO();
    out.push(`taste_ledger:`);
    p.tasteLedger.forEach(rule => {
      out.push(`  - date: ${yamlQuote(today)}`);
      out.push(`    rule: ${yamlQuote(rule)}`);
    });
  }

  // vocabulary: only if the operator seeded (valid) entries.
  if (p.vocabulary.length > 0) {
    out.push(`vocabulary:`);
    p.vocabulary.forEach(v => {
      out.push(`  - term: ${yamlQuote(v.term)}`);
      out.push(`    meaning: ${yamlQuote(v.meaning)}`);
    });
  }

  return out.join("\n") + "\n";
}

// Split "term: meaning" lines into parsed entries and a list of malformed
// lines (non-empty lines with no usable "term: meaning" shape). The term is
// everything before the FIRST colon; the meaning is the rest.
function parseVocabulary(textareaValue) {
  const entries = [];
  const malformed = [];
  textareaValue.split("\n").forEach(raw => {
    const line = raw.trim();
    if (line.length === 0) return;
    const idx = line.indexOf(":");
    if (idx < 0) {
      malformed.push(line);
      return;
    }
    const term = line.slice(0, idx).trim();
    const meaning = line.slice(idx + 1).trim();
    if (term.length === 0 || meaning.length === 0) {
      malformed.push(line);
      return;
    }
    entries.push({ term: term, meaning: meaning });
  });
  return { entries: entries, malformed: malformed };
}

function readPreferences() {
  const primarySel = document.getElementById("prefs-primary-lang").value;
  let primaryLang;
  if (primarySel === "other") {
    primaryLang = (document.getElementById("prefs-primary-lang-other").value || "").trim();
  } else {
    primaryLang = primarySel;
  }
  const reports = document.querySelector('input[name="prefs-reports"]:checked').value;
  const reportStyle = document.querySelector('input[name="prefs-report-style"]:checked').value;
  const decisionFormat = document.querySelector('input[name="prefs-decision-format"]:checked').value;
  const batchQuestions = document.getElementById("prefs-batch-questions").checked;
  const autonomyProfile = document.querySelector('input[name="prefs-autonomy"]:checked').value;
  const quietHours = (document.getElementById("prefs-quiet-hours").value || "").trim();
  const timezone = (document.getElementById("prefs-timezone").value || "").trim();
  const tasteLedger = lines(document.getElementById("prefs-taste-ledger").value);
  const vocab = parseVocabulary(document.getElementById("prefs-vocabulary").value);

  return {
    primarySel: primarySel,
    primaryLang: primaryLang,
    reports: reports,
    reportStyle: reportStyle,
    decisionFormat: decisionFormat,
    batchQuestions: batchQuestions,
    autonomyProfile: autonomyProfile,
    quietHours: quietHours,
    timezone: timezone,
    tasteLedger: tasteLedger,
    vocabulary: vocab.entries,
    vocabularyMalformed: vocab.malformed,
  };
}

function readForm() {
  const ownerLogin = (document.getElementById("owner-login").value || "").trim();
  const allowlistedExtra = lines(document.getElementById("owner-allowlist").value);
  const displayName = (document.getElementById("owner-display").value || "").trim();
  const governanceRepo = (document.getElementById("governance-repo").value || "").trim();
  const supervisedRepos = lines(document.getElementById("supervised-repos").value);
  const botRepo = (document.getElementById("bot-repo").value || "").trim();
  const runnerTier = document.querySelector('input[name="runner-tier"]:checked').value;
  const skillHallucination = document.getElementById("skill-hallucination").checked;
  const skillEmptyPr = document.getElementById("skill-empty-pr").checked;
  const botAppSlug = (document.getElementById("bot-app-slug").value || "").trim();
  const agents = Array.from(
    document.querySelectorAll(".agent-checkboxes input:checked")
  ).map(el => el.value);
  const preferences = readPreferences();

  return {
    ownerLogin,
    allowlistedExtra,
    displayName,
    governanceRepo,
    supervisedRepos,
    botRepo,
    runnerTier,
    skillHallucination,
    skillEmptyPr,
    botAppSlug,
    agents,
    preferences,
  };
}

function validateState(state) {
  const errors = [];
  if (!/^[A-Za-z0-9][A-Za-z0-9-]{0,38}$/.test(state.ownerLogin)) {
    errors.push("Step 1: 'Your GitHub login' is required and must be a valid GitHub login.");
  }
  if (!/^[A-Za-z0-9][A-Za-z0-9-]{0,38}\/[A-Za-z0-9._-]{1,100}$/.test(state.governanceRepo)) {
    errors.push("Step 2: 'Governance repo' is required and must be in owner/repo form.");
  }
  state.supervisedRepos.forEach(r => {
    if (!/^[A-Za-z0-9][A-Za-z0-9-]{0,38}\/[A-Za-z0-9._-]{1,100}$/.test(r)) {
      errors.push(`Step 2: supervised repo '${r}' is not in owner/repo form.`);
    }
  });
  if (state.botRepo && !/^[A-Za-z0-9][A-Za-z0-9-]{0,38}\/[A-Za-z0-9._-]{1,100}$/.test(state.botRepo)) {
    errors.push("Step 2: 'Bot repo' is set but not in owner/repo form.");
  }
  if (state.botAppSlug && !/^[a-z0-9][a-z0-9-]*[a-z0-9]$/.test(state.botAppSlug)) {
    errors.push("Step 6: 'GitHub App slug' must be lowercase letters, numbers, and hyphens.");
  }

  const p = state.preferences;
  if (p.primarySel === "other" && !/^.{2,16}$/.test(p.primaryLang)) {
    errors.push("Step 3: primary language 'Other' needs a code of 2-16 characters.");
  }
  if (p.quietHours && !/^([01][0-9]|2[0-3]):[0-5][0-9]-([01][0-9]|2[0-3]):[0-5][0-9]$/.test(p.quietHours)) {
    errors.push("Step 3: quiet hours must look like HH:MM-HH:MM (24h clock), e.g. 22:30-07:30.");
  }
  return errors;
}

function buildAllFiles(state) {
  return {
    "config/owner.yml": generateOwnerYml(state),
    "config/projects.yml": generateProjectsYml(state),
    "config/env.yml": generateEnvYml(state),
    "config/skills.yml": generateSkillsYml(state),
    "config/agent_registry.yml": generateAgentRegistryYml(state),
    "config/preferences.yml": generatePreferencesYml(state.preferences),
  };
}

// -----------------------------------------------------------------------------
// ZIP download (without external dependencies — handcrafted minimal ZIP).
// We use a tiny ZIP builder. For very small zips this is fine; if anyone
// needs encryption or large files, they should use a real library.
// -----------------------------------------------------------------------------

function downloadZip(files) {
  // Each "file" is { name, data: Uint8Array }
  const entries = Object.entries(files).map(([name, content]) => ({
    name,
    data: new TextEncoder().encode(content),
  }));
  const zip = makeZip(entries);
  const blob = new Blob([zip], { type: "application/zip" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "multiagent-protocol-config.zip";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

// Build a STORE-method (uncompressed) ZIP. ZIP STORE is the dead-simplest
// format that any unzip tool understands.
function makeZip(entries) {
  const crcTable = makeCrcTable();
  const localParts = [];
  const centralParts = [];
  let offset = 0;
  for (const e of entries) {
    const nameBytes = new TextEncoder().encode(e.name);
    const crc = crc32(e.data, crcTable);
    const size = e.data.length;

    // Local file header (30 bytes + name + data)
    const localHeader = new Uint8Array(30 + nameBytes.length);
    const lv = new DataView(localHeader.buffer);
    lv.setUint32(0, 0x04034b50, true);   // signature
    lv.setUint16(4, 20, true);            // version
    lv.setUint16(6, 0, true);             // flags
    lv.setUint16(8, 0, true);             // STORE
    lv.setUint16(10, 0, true);            // time
    lv.setUint16(12, 0, true);            // date
    lv.setUint32(14, crc, true);
    lv.setUint32(18, size, true);
    lv.setUint32(22, size, true);
    lv.setUint16(26, nameBytes.length, true);
    lv.setUint16(28, 0, true);            // extra
    localHeader.set(nameBytes, 30);
    localParts.push(localHeader, e.data);

    // Central directory header (46 + name)
    const centralHeader = new Uint8Array(46 + nameBytes.length);
    const cv = new DataView(centralHeader.buffer);
    cv.setUint32(0, 0x02014b50, true);
    cv.setUint16(4, 20, true);
    cv.setUint16(6, 20, true);
    cv.setUint16(8, 0, true);
    cv.setUint16(10, 0, true);
    cv.setUint16(12, 0, true);
    cv.setUint16(14, 0, true);
    cv.setUint32(16, crc, true);
    cv.setUint32(20, size, true);
    cv.setUint32(24, size, true);
    cv.setUint16(28, nameBytes.length, true);
    cv.setUint16(30, 0, true);
    cv.setUint16(32, 0, true);
    cv.setUint16(34, 0, true);
    cv.setUint16(36, 0, true);
    cv.setUint32(38, 0, true);
    cv.setUint32(42, offset, true);
    centralHeader.set(nameBytes, 46);
    centralParts.push(centralHeader);

    offset += localHeader.length + e.data.length;
  }

  const centralSize = centralParts.reduce((a, b) => a + b.length, 0);
  const centralStart = offset;

  // End of central directory
  const end = new Uint8Array(22);
  const ev = new DataView(end.buffer);
  ev.setUint32(0, 0x06054b50, true);
  ev.setUint16(4, 0, true);
  ev.setUint16(6, 0, true);
  ev.setUint16(8, entries.length, true);
  ev.setUint16(10, entries.length, true);
  ev.setUint32(12, centralSize, true);
  ev.setUint32(16, centralStart, true);
  ev.setUint16(20, 0, true);

  // Concatenate.
  const total = offset + centralSize + 22;
  const out = new Uint8Array(total);
  let pos = 0;
  for (const p of localParts) { out.set(p, pos); pos += p.length; }
  for (const p of centralParts) { out.set(p, pos); pos += p.length; }
  out.set(end, pos);
  return out;
}

function makeCrcTable() {
  const t = new Uint32Array(256);
  for (let i = 0; i < 256; i++) {
    let c = i;
    for (let j = 0; j < 8; j++) {
      c = (c & 1) ? (0xedb88320 ^ (c >>> 1)) : (c >>> 1);
    }
    t[i] = c >>> 0;
  }
  return t;
}

function crc32(data, table) {
  let crc = 0xffffffff;
  for (let i = 0; i < data.length; i++) {
    crc = (crc >>> 8) ^ table[(crc ^ data[i]) & 0xff];
  }
  return (crc ^ 0xffffffff) >>> 0;
}

// -----------------------------------------------------------------------------
// GitHub App manifest URL builder
// -----------------------------------------------------------------------------

function buildManifest(state) {
  const name = state.botAppSlug || "merge-gate-bot";
  return {
    name: name,
    url: `https://github.com/${state.governanceRepo}`,
    hook_attributes: {
      // No webhooks needed; the bot uses cron.
      url: "https://example.com/unused",
    },
    redirect_url: `https://github.com/${state.governanceRepo}`,
    public: false,
    default_permissions: {
      contents: "write",
      pull_requests: "write",
      issues: "write",
      checks: "write",
      workflows: "read",
      metadata: "read",
      actions: "read",
    },
    default_events: [],
  };
}

function buildManifestUrl(state) {
  // GitHub expects the manifest in a `manifest` query param, URL-encoded.
  const json = JSON.stringify(buildManifest(state));
  return `https://github.com/settings/apps/new?manifest=${encodeURIComponent(json)}`;
}

// -----------------------------------------------------------------------------
// Delegation prompt: the product's core UX. The operator pastes this to THEIR
// OWN AI coding agent, which performs the WHOLE installation by fetching and
// following the upstream AGENT_SETUP.md runbook. Step numbers 0-9 are a FIXED
// contract with that runbook — do not renumber them here without updating it.
// -----------------------------------------------------------------------------

// Canonical upstream — the public framework repo. This is the ONLY real
// identifier allowed in wizard output; everything else is the operator's own.
const UPSTREAM_REPO = "donggun-jung/multiagent-protocol";
const UPSTREAM_BRANCH = "main";
const SETUP_DOC_RAW =
  "https://raw.githubusercontent.com/" + UPSTREAM_REPO + "/" +
  UPSTREAM_BRANCH + "/docs/agent-setup/AGENT_SETUP.md";

function buildAgentPrompt(state, files) {
  const repo = state.governanceRepo || "<your-governance-repo>";
  const slug = state.botAppSlug || "your-merge-gate";
  const embedded = Object.entries(files).map(([name, content]) =>
    "--- " + name + " ---\n" + content.replace(/\n$/, "")
  ).join("\n\n");

  return `You are my AI coding agent. Install multiagent-protocol for me end to end.
I am the operator; you do the work and only involve me at the steps marked
[HUMAN]. Verify each step before moving on; if a verification fails twice,
STOP and report the step number — do not improvise a workaround.

First, fetch and follow this runbook exactly (its step numbers are the contract
below): ${SETUP_DOC_RAW}
It is the canonical procedure from the public framework repo
${UPSTREAM_REPO} (branch ${UPSTREAM_BRANCH}). My governance repo is \`${repo}\`.

Step 0 — Preflight: confirm gh is authenticated as me, git + python 3 present,
  and that the runbook's version matches this prompt's step contract.
Step 1 — Create my PRIVATE governance repo \`${repo}\` as a MIRROR of the
  upstream (git clone --mirror upstream, push into a fresh private repo). Do NOT
  fork: a fork of a public repo cannot be made private.
Step 2 — Write the 6 config files below into \`config/\` exactly as given, then
  validate with \`python -m multiagent_protocol check-config\`. Fix and re-run
  until it prints "config OK".
Step 3 — Deploy the cron workflow from \`deploy/bot-cron.example.yml\` into the
  governance repo's \`.github/workflows/\` (pick the cron cadence from the
  runbook's step-3 table; the runner tier comes from config/env.yml).
Step 4 — [HUMAN] GitHub App: prepare the App-manifest registration URL and give
  it to me to click (this is the ONE human-click step). After I register the App
  and hand you the App ID + private key, set the repo secrets:
  \`gh secret set MERGE_GATE_APP_ID\`, \`gh secret set MERGE_GATE_PRIVATE_KEY\`,
  and generate + set \`gh secret set MERGE_GATE_RECEIPT_KEY\` (a fresh random key).
Step 5 — Prepare each supervised repo: create the \`ready-to-merge\` label, turn
  on squash-merge, and ensure CI exists (or set allow_no_ci per the runbook).
Step 6 — Install the \`templates/adopter\` kit into each supervised repo with the
  placeholders filled — including the preferences block rendered from
  config/preferences.yml (language, report style, autonomy, taste ledger,
  vocabulary).
Step 7 — Run the first tick in OBSERVE mode (no merges); confirm the bot reads
  config and classifies open PRs without acting.
Step 8 — [HUMAN] Go-live: once I confirm observe output looks right, set the repo
  variable \`MERGE_GATE_MERGE_ENABLED=true\`.
Step 9 — End-to-end test: open a trivial PR that should auto-merge, confirm the
  bot merges it, then give me a short handover report (what is live, what I own).

App slug to register in Step 4: \`${slug}\`.

The 6 config files to write in Step 2:

${embedded}
`;
}

// -----------------------------------------------------------------------------
// Preferences step: dynamic UI (language "other" toggle + vocabulary warning)
// -----------------------------------------------------------------------------

// Show or hide the free-text language input based on the select.
function syncPrimaryLangOther() {
  const sel = document.getElementById("prefs-primary-lang").value;
  const wrap = document.getElementById("prefs-primary-lang-other-wrap");
  if (sel === "other") {
    wrap.classList.remove("hidden");
  } else {
    wrap.classList.add("hidden");
  }
}

// Recompute and display the inline warning for malformed vocabulary lines.
// Gentle: it never blocks generation — malformed lines are simply skipped.
function refreshVocabWarning() {
  const el = document.getElementById("prefs-vocab-warning");
  if (!el) return;
  const parsed = parseVocabulary(document.getElementById("prefs-vocabulary").value);
  if (parsed.malformed.length === 0) {
    el.classList.add("hidden");
    el.textContent = "";
    return;
  }
  const template = t("prefs.vocab_warning");
  el.textContent = template.replace("{lines}", parsed.malformed.join(", "));
  el.classList.remove("hidden");
}

document.getElementById("prefs-primary-lang").addEventListener("change", syncPrimaryLangOther);
document.getElementById("prefs-vocabulary").addEventListener("input", refreshVocabWarning);
syncPrimaryLangOther();

// -----------------------------------------------------------------------------
// Wire up the button handlers
// -----------------------------------------------------------------------------

let lastGenerated = null;
let lastState = null;

document.getElementById("generate").addEventListener("click", () => {
  const state = readForm();
  const errors = validateState(state);

  const output = document.getElementById("output");
  const preview = document.getElementById("preview-files");

  if (errors.length > 0) {
    alert("Please fix:\n\n" + errors.join("\n"));
    return;
  }

  const files = buildAllFiles(state);
  lastGenerated = files;
  lastState = state;

  preview.innerHTML = "";
  Object.entries(files).forEach(([name, content]) => {
    const div = document.createElement("div");
    div.className = "file-block";
    const h = document.createElement("h4");
    h.textContent = name;
    const pre = document.createElement("pre");
    pre.textContent = content;
    div.appendChild(h);
    div.appendChild(pre);
    preview.appendChild(div);
  });

  output.classList.remove("hidden");
  document.getElementById("open-manifest").disabled = false;

  // Manual fallback: expose the registration URL + raw manifest JSON so the
  // user is not stuck if the pop-up is blocked or the URL is too long.
  document.getElementById("manifest-url").value = buildManifestUrl(state);
  document.getElementById("manifest-json").value = JSON.stringify(buildManifest(state), null, 2);
  document.getElementById("manifest-fallback").classList.remove("hidden");
});

document.getElementById("download-zip").addEventListener("click", () => {
  if (!lastGenerated) return;
  downloadZip(lastGenerated);
});

document.getElementById("copy-prompt").addEventListener("click", async () => {
  if (!lastGenerated || !lastState) return;
  const prompt = buildAgentPrompt(lastState, lastGenerated);
  try {
    await navigator.clipboard.writeText(prompt);
    const btn = document.getElementById("copy-prompt");
    const orig = btn.textContent;
    btn.textContent = "✓ copied";
    setTimeout(() => { btn.textContent = orig; }, 1500);
  } catch (e) {
    alert("Could not copy to clipboard. Try selecting the prompt manually:\n\n" + prompt.slice(0, 200) + "...");
  }
});

document.getElementById("open-manifest").addEventListener("click", () => {
  if (!lastState) return;
  const url = buildManifestUrl(lastState);
  window.open(url, "_blank", "noopener,noreferrer");
});
