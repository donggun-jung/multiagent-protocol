---
schema_version: 1
adr_number: 4
title: "Exact-object version-truth completion subreceipts"
status: accepted
date: 2026-08-26
authors: ["<owner-github-login>"]
supersedes: null
related:
  - docs/guide/version-truth-completion.md
  - schemas/project_completion_receipt.schema.json
  - SECURITY.md
---

## Context

A declared version check can report green while reading a mutable registry
working tree, a stale local product ref, or a registry-selected alternate state
file. A pre/post shell transcript can reduce those risks, but it does not make
the registry and product reads one atomic, machine-checkable contract. Git
replacement refs and ambient URL rewrite configuration add less visible object
substitution paths.

At the same time, repository state alone cannot prove which deployment instance
ran, which artifact it produced, or what a live endpoint serves. Calling a Git
parity result "deployment complete" would create a different false-green.

The public framework also cannot hardcode one operator's governance repository.
The intended registry origin must enter as a protected caller-owned trust
anchor and remain visible in the receipt.

## Decision

Add a `check-project --completion` profile with a distinct
`ProjectCompletionReceipt` schema.

For both registry and product, the checker:

1. probes canonical `refs/heads/main` directly;
2. fetches it into an isolated temporary bare repository;
3. requires the fetched commit OID to equal the probed tip;
4. reads the fixed `OID:path` blob once as binary data and uses the same bytes
   for strict YAML parsing and SHA-256;
5. disables Git replacement objects and binds empty pre/post
   `refs/replace` results; and
6. probes the remote tip again, failing if it moved.

The registry path is fixed to `governance/projects.yml`; a parity product state
path is fixed to root `VERSION_STATE.yml`. Completion requires a valid
`release_id_pattern` to fullmatch the registry baseline, product deployed
version, and every non-`none` pending version. Missing identifiers fail even if
the regex could match an empty string.

The successful status is
`DECLARED_STATE_COMPLETION_SUBRECEIPT_OK`, not `COMPLETION_OK`, and every receipt
sets `completion_authorized: false`. The receipt carries a non-exhaustive list
of unverified dimensions, including deployment causality, live readback,
artifact provenance, nonce authority, and an authoritative deployment
sequence. Its self-SHA covers deterministic canonical JSON with the hash member
excluded; it is content integrity, not author authentication.

Add a separate `check-registry` guard. It compares the canonical current
registry regular file with the same path at an exact full baseline commit OID.
A prior `legacy-declared-parity` row cannot be deleted. Removing or changing
that contract requires exact matching supersession metadata in the surviving
row and strict frontmatter in an accepted in-repository ADR. Registered regexes
must compile, and parity rows must keep the fixed root state path.
The ADR's `supersedes` value must be a non-empty path array; the normal
`supersedes: null` marker means there is no supersession and cannot authorize a
transition.

## Consequences

- Mutable checkout state, stale local refs, alternate state paths, release-ID
  format gaps, replacement refs, and observed remote-tip movement fail closed.
- Registry/product origin URLs, refspecs, OIDs, blob hashes, command timing and
  exit evidence, exact argv, task ID, scope, and receipt hash method are
  machine-readable.
- The nested `ProjectCheck` keeps its established field names. The new evidence
  is additive and the completion envelope has its own kind/schema.
- Callers must protect the registry-origin slug as a trust anchor and provide
  the actual review-base commit OID to `check-registry`.
- Product-specific deploy receipts and live adapters remain separate work. A
  green declared-state subreceipt alone can never authorize a completion claim.

## Alternatives considered

- **Reuse a clean working checkout.** Rejected: cleanliness and before/after
  evidence still split the registry bytes, ref movement, and verdict across
  mutable observations.
- **Trust a local `origin/main`.** Rejected: it can be stale or configured to a
  fork. Direct remote probes and fetched-OID equality are required.
- **Hardcode one governance slug in the package.** Rejected: the framework is
  multi-operator and tracked code must not contain personal installation data.
  A protected caller-provided expected slug is the portable trust boundary.
- **Call the declared-state result final completion.** Rejected: Git parity
  cannot prove deployment-instance causality or live state. The restricted
  status and `completion_authorized: false` are load-bearing.
- **Invent a local nonce or infer sequence from release IDs.** Rejected: a
  trusted nonce needs an independent issuer and single-use ledger; a deployment
  sequence must come from a serialized control plane and advance on rollback.
- **Allow contract removal with a comment.** Rejected: approval evidence must
  be machine-readable, exact, and durable in an accepted ADR.
