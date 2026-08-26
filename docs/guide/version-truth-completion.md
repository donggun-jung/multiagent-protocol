# Version-truth completion receipts

`check-project --completion` verifies one narrow claim: a version registry and
a product's declared version state agree at exact, stable remote Git objects.
It emits one JSON document conforming to
[`project_completion_receipt.schema.json`](../../schemas/project_completion_receipt.schema.json).

It does **not** prove that a deployment ran or that a live endpoint serves the
declared release. Even a successful receipt has:

```json
{
  "status": "DECLARED_STATE_COMPLETION_SUBRECEIPT_OK",
  "completion_authorized": false
}
```

Treat it as a component of a deployment envelope, never as the final deployment
authorization.

## Run a completion check

The registry origin is a trust anchor supplied by the caller. Put the expected
slug in protected automation configuration rather than deriving it from a
working checkout or from the registry itself.

```sh
multiagent-protocol check-project service-a \
  --completion \
  --deployment-task-id deploy-532 \
  --registry-origin-slug example-org/company-operations
```

The slug defaults to the corresponding canonical HTTPS URL. A separately
supplied URL must still resolve to the same slug.

```sh
multiagent-protocol check-project service-a \
  --completion \
  --deployment-task-id deploy-532 \
  --registry-origin-slug example-org/company-operations \
  --registry-origin-url https://github.com/example-org/company-operations.git
```

Private registries and products use one explicit credential channel while the
ambient Git configuration remains disabled. Supply a helper command that does
not contain a literal secret, and, when the registry's `repo` transport is not
usable on the runner, bind a canonical product transport URL with the same
owner/repository slug:

```sh
multiagent-protocol check-project service-a \
  --completion \
  --deployment-task-id deploy-532 \
  --registry-origin-slug example-org/company-operations \
  --git-credential-helper '!gh auth git-credential' \
  --product-origin-url https://github.com/example-org/service-a.git
```

The helper is injected as a per-process `git -c credential.helper=<command>`
argument; no credential helper is recovered from global or system config. The
exact injected `-c` arguments and resolved registry/product transport URLs are
recorded in `registry_binding` and `product_binding`. Because the command also
appears in the receipt and exact argv, reference a credential source such as a
helper or environment variable—never put a token value in this option.

The completion mode rejects mutable legacy inputs, including `--registry`,
`--registry-repo`, `--path`, `--base-dir`, `--allow-working-tree`,
`--skip-fetch`, and a `--ref` other than `origin/main`. Argument failures are
also emitted as one JSON receipt; argparse text is not mixed into stdout.

The canonical registry object path is fixed to
`governance/projects.yml`. A parity row used for completion must provide at
least:

```yaml
schema_version: 1
projects:
  - id: service-a
    repo: https://github.com/example-org/service-a.git
    repo_slug: example-org/service-a
    version_contract: legacy-declared-parity
    version_state: VERSION_STATE.yml
    deployed_baseline: rel-7
    release_id_pattern: "^rel-[0-9]+$"
```

The product object path is always root `VERSION_STATE.yml`:

```yaml
schema_version: 1
expected_remote_slug: example-org/service-a
deployed_version: rel-7
pending_version: none
```

`release_id_pattern` must be non-blank and no longer than 1024 characters. It is
compiled before use and applied with full-string
matching to `deployed_baseline`, `deployed_version`, and every non-`none`
`pending_version`. The deployed values must be equal; a non-`none` pending value
must differ from the deployed value. Missing or empty pending state fails
closed. Python's regular-expression engine has no execution timeout; residual
pattern-complexity exposure is intentionally low because the registry is the
caller-protected trust anchor, not product-controlled input.

## What is bound

Each run uses separate temporary bare repositories and performs these steps:

1. Probe the registry's exact `refs/heads/main` tip with `ls-remote`.
2. Fetch that branch into an isolated ref and require the fetched commit OID to
   equal the probed tip.
3. Read `OID:governance/projects.yml` once as binary stdout. The same byte
   object is SHA-256 hashed and strict-YAML parsed; no `.strip()` or working-tree
   read occurs.
4. Select the product remote from those pinned registry bytes. Reject any
   `version_state` value other than root `VERSION_STATE.yml`.
5. Repeat the remote-tip, exact fetch, single blob read, hash, and parse for
   `OID:VERSION_STATE.yml`.
6. Apply parity and all three release-pattern checks.
7. Probe both remote tips again. Either tip changing makes the run fail and the
   entire check must be rerun.
8. Confirm `refs/replace` is empty before and after each object read.
9. Emit one self-hashed JSON receipt after the final probes.

Every Git subprocess sets `GIT_NO_REPLACE_OBJECTS=1`, disables global and
system Git configuration, removes Git topology-injection environment variables,
and disables terminal prompting. The receipt records the actually applied
required environment, transport URL, injected Git config arguments, canonical
slug, refspec, before/fetched/after OIDs, fetch argv/exit/timestamps, tip-probe
argv/exit/timestamps, Git object format, blob OID, exact-byte SHA-256, read
count, and replacement-ref results. `project_check.input_binding.freshness`
reports `fetched` only after an observed successful fetch and otherwise reports
`null`; `remote_tip_stability` is derived from successful probes as
`before-and-after-checked`, `before-only`, or `not-probed`.

The nested `project_check` retains the established `ProjectCheck` field names
and adds binding evidence under `input_binding`. Existing consumers can keep
reading fields such as `status`, `ok`, `reasons`, `pending_reasons`,
`deployed_baseline`, and `input_binding.product_ref_oid`.

## Receipt integrity and exit codes

The receipt binds the supplied `deployment_task_id`, exact process argv,
generation timestamp, and intended return code. A process cannot observe its
own final operating-system exit after it has emitted output, so the receipt says
that this code is self-reported and lists final-exit observation as unverified.
An outer deployment supervisor should record the command's actually observed
exit beside the receipt.

`receipt_sha256` is computed as follows:

- deep-copy the receipt;
- omit the `/receipt_sha256` member;
- encode UTF-8 JSON with `sort_keys=true`, compact `(',', ':')` separators, and
  `ensure_ascii=false`;
- calculate SHA-256 over those bytes.

The exact method is in `receipt_sha256_method`. The library function
`verify_receipt_sha256()` verifies it. This unkeyed digest detects accidental or
unrecorded content changes; it does not authenticate the author or storage.

Exit codes are stable:

| Exit | Meaning |
|---:|---|
| `0` | Exact declared-state bindings passed; not live deployment authorization |
| `1` | Binding, state, parity, or internal check failed |
| `2` | CLI usage was invalid or a mutable completion input was requested |
| `4` | The strict YAML runtime dependency was unavailable |

Failure receipts are sealed too. They contain at least one reason and leave
`verified_dimensions` empty rather than overstating partial evidence.

## Guard registry transitions

Run `check-registry` in the registry repository against the exact full commit
OID that is the review baseline:

```sh
multiagent-protocol check-registry \
  --repo-root . \
  --registry governance/projects.yml \
  --baseline-ref <full-base-commit-oid>
```

Abbreviated revisions, branches, and tags are rejected. The guard reads the
baseline registry as
`<full-base-commit-oid>:governance/projects.yml` with replacement objects
disabled, hashes and parses the same baseline bytes, then compares it with the
current file. The current path must be that exact repo-root regular file;
symlinks and alternate paths are rejected. It checks:

- every registered `release_id_pattern` is a non-empty, compilable regex;
- an omitted parity `version_state` uses the canonical `VERSION_STATE.yml`
  default, while every explicit non-canonical value is rejected;
- a previous parity row cannot be deleted; and
- a parity contract cannot be removed or changed without exact supersession
  evidence.

An authorized transition needs matching evidence in both the current row and
an accepted, in-repository ADR. The row form is:

```yaml
version_contract: release-manifest
version_contract_supersession:
  from: legacy-declared-parity
  to: release-manifest
  adr: docs/decisions/0001-version-contract.md
```

The referenced ADR starts with strict YAML frontmatter:

```yaml
---
status: accepted
supersedes:
  - docs/decisions/0000-prior-contract.md
version_contract_supersession:
  project_id: service-a
  from: legacy-declared-parity
  to: release-manifest
---
```

All fields must agree exactly. `supersedes` must be a non-empty array of
non-empty in-repository path strings; YAML `null` and a scalar string are not
approval evidence. Missing evidence, extra evidence keys, unsafe ADR paths,
unreadable frontmatter, or non-accepted status fails closed.

Every `RegistryCheck` receipt lists substantive ADR review and merge
authorization as unverified. The command verifies documentary structure and
cross-file agreement; the merge gate and human review provide the actual
authorization strength.

## Deliberately unverified dimensions

The profile always emits a non-exhaustive `unverified_dimensions` array. In
particular:

| Dimension | Why it remains outside this profile |
|---|---|
| Deployment-instance causality | Git state cannot show that this invocation caused a deployment |
| Live identity/source readback | Products do not share one endpoint or manifest contract |
| Source-to-artifact provenance | A source OID and artifact hash need a build/deploy attestation chain |
| Trusted nonce authenticity and uniqueness | This needs an independent issuer, expiry rules, and a single-use ledger |
| Authoritative monotonic deployment sequence | This must come from a serialized deployment control plane and be exposed by live readback |
| Remote-tip ABA between probes | Equal before/after tips do not prove the ref never changed in between |
| Registry-origin root of trust | If an attacker controls both slug and argv, the checker cannot discover the intended governance remote |
| Receipt storage authenticity | A plain SHA-256 is not a signature or an immutable receipt store |

A local random UUID is not a trusted nonce. A release number is not a deployment
sequence: a rollback is a new deployment and must advance the authoritative
sequence too. Those mechanisms should be added only with a named issuer,
verification relation, replay policy, durable ledger, live exposure, and
serialization boundary.
