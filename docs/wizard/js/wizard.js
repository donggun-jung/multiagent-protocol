// multiagent-protocol setup wizard — pure-client config generator.
// No network calls. Reads form, builds YAML strings, offers download + App Manifest URL.

// -----------------------------------------------------------------------------
// Language switching
// -----------------------------------------------------------------------------

document.querySelectorAll(".lang-switch button").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".lang-switch button").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    applyLocale(btn.getAttribute("data-lang"));
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
    errors.push("Step 5: 'GitHub App slug' must be lowercase letters, numbers, and hyphens.");
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
// Agent-assist prompt: a copy-pasteable prompt for handing the wizard's
// output to an AI coding agent for the rest of the install.
// -----------------------------------------------------------------------------

function buildAgentPrompt(state, files) {
  const repo = state.governanceRepo || "<your-fork>/multiagent-protocol";
  return `I have generated config files for my multiagent-protocol installation.
Please help me complete the setup. The config files (5 files under config/)
are pasted below. Apply them to the repo \`${repo}\` (fork of
https://github.com/donggun-jung/multiagent-protocol):

1. Create a feature branch, e.g. \`setup/initial-config\`.
2. Write the 5 files exactly as shown.
3. Commit with subject \`setup: initial wizard config\` and include
   the standard Agent-* commit trailers.
4. Push and open a PR against \`main\`.
5. Tell me the manifest URL to register the GitHub App
   (you can read the wizard's URL builder logic from
   docs/wizard/js/wizard.js function buildManifestUrl).
6. Wait for me to confirm the App is registered + Actions secrets are set
   (MERGE_GATE_APP_ID + MERGE_GATE_PRIVATE_KEY), then merge the PR.

Files to write:

${Object.entries(files).map(([name, content]) =>
  `--- ${name} ---\n${content}`
).join("\n")}
`;
}

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
