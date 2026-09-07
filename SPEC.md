# One Hive R0: Immutable Git Pinning Specification

**Status:** Frozen — implemented by [`hivepin`](README.md) 1.0  
**Version:** 1.0  
**Target release:** R0  
**Primary consumers:** One Hive record, projections, worker runtime, skill registry, review harness, replay tooling

> ### v1.0 as-built notes
>
> This document is the frozen contract. `hivepin` 1.0 implements all of it. Where
> the original draft left a choice open, v1.0 resolved it as follows — these
> resolutions are part of the frozen contract:
>
> | Area | Resolution |
> |---|---|
> | Registry (§7) | optional `local_path` per repository — a working clone or mirror used to read objects at mint time. `verify`/`materialize` can otherwise run from the publication cache. |
> | Reversible label (§9) | `hivepin:v1:<base64url(canonical-json)>`. The `.pin` file is the raw canonical object plus one trailing LF. |
> | Tree manifest (§11.2) | entry object is exactly `{"digest","mode","path","size"}`, keys code-point ordered; entries ordered by the UTF-8 bytes of the NFC-normalized path; a post-NFC collision is `PATH_COLLISION`. |
> | Publication (§13) | `git ls-remote` each fetch URL, keep refs matching `allowed_ref_patterns`, fetch them into `<cache_directory>/<repo-id>.git` under `refs/hivepin/*` (never the caller's refs, never `FETCH_HEAD`), then test ancestry there. |
> | Limits (§17) | exceeding a configured limit raises the dedicated code **`LIMIT_EXCEEDED`**. |
> | Exit codes (§15) | `INVALID_PIN`, `UNSUPPORTED_VERSION`, `INVALID_PATH`, `INVALID_REGISTRY`, `DESTINATION_EXISTS` → `2`; every other verification-class code → `3`; `REMOTE_UNAVAILABLE` → `4`; `INTERNAL_ERROR` → `5`. |
> | `--offline` on `mint` | permitted; yields `publication_status: "not_checked"`, which is **not** authoritative. |
>
> **Deliberately deferred** (a new schema version, never improvised into v1):
> symlink/submodule *support*, recursive submodule pins, signed pin envelopes,
> artifact-store integration, cross-repository manifests, alternative digest
> algorithms, provenance/review receipts, repository-id migration records.

## 1. Purpose

R0 defines a durable and unambiguous way to reference a file or directory at an exact Git revision. It also provides a small library and command-line tool for creating, verifying, and materializing those references.

The result is called a **pin**. A pin must continue identifying the same bytes regardless of later edits, renames, branch movement, working-directory state, or which machine performs the resolution. Materialization additionally requires the registered repository or an operator cache to retain the referenced Git objects; permanent remote retention is outside R0.

R0 is a protocol and utility release. It does not implement the hive event record, reviews, authorization, agent identity, or artifact promotion.

## 2. Required outcomes

R0 must provide:

1. A frozen v1 pin schema.
2. A versioned repository registry schema.
3. A library implementing `mint`, `verify`, and `materialize`.
4. A CLI exposing those operations.
5. Deterministic file and directory hashing.
6. Machine-readable results and stable error codes.
7. Automated conformance, security, and cross-clone tests.
8. An adoption note from at least one hive other than the development team's test hive.

## 3. Normative language

The terms **MUST**, **MUST NOT**, **SHOULD**, **SHOULD NOT**, and **MAY** describe implementation requirements.

## 4. Non-goals

R0 does not provide:

- proof that referenced content is correct, safe, reviewed, or authorized;
- signatures, attestations, or a hash-chained event ledger;
- a general artifact store or garbage collector;
- cross-repository directory trees or submodule recursion;
- symlink materialization;
- mutable aliases such as `main`, tags, or branch names in canonical pins;
- Git checkout or working-tree management;
- automatic migration of future pin versions.

A pin proves content identity and, when checked online, remote availability. Later releases establish provenance, review, authority, and lifecycle semantics.

## 5. Terminology

| Term | Meaning |
|---|---|
| Repository ID | Stable logical name for a repository, resolved through the repository registry. |
| Commit OID | Full Git object identifier of a commit. Never an abbreviated SHA or mutable ref. |
| Object OID | Full Git object identifier of the file blob or directory tree at the pinned path. |
| Content digest | Independent SHA-256 digest of the materialized file bytes or canonical tree manifest. |
| Published commit | A commit reachable from an allowed ref advertised by a trusted registered remote. |
| Authoritative pin | A canonical pin eligible to be recorded in the hive. It must reference published content. |
| Materialization | Safe extraction of the pinned content into a new local directory. |

## 6. Core invariants

An implementation must maintain these invariants:

1. **Exact identity:** a pin identifies one repository, commit, path, object kind, Git object, mode, and content digest.
2. **No mutable refs:** canonical pins never contain a branch, tag, `HEAD`, or abbreviated object ID.
3. **Published availability:** authoritative pins reference commits reachable from an allowed ref on a trusted remote.
4. **No ambient repository inference:** verification never derives repository identity from the current directory alone.
5. **No path escape:** pinned and materialized paths cannot escape their repository or destination roots.
6. **No implicit dereferencing:** v1 rejects symlinks and submodules.
7. **Determinism:** two conforming implementations given the same registered repository and content produce byte-identical canonical pins.
8. **Verify before release:** materialized output is not exposed at the destination until all identity and content checks pass.
9. **No hooks or checkout:** pin operations must not execute repository hooks or alter the caller's working tree.

## 7. Repository registry

Every `repository` value in a pin must resolve through a versioned registry controlled by the hive operator.

Example:

```json
{
  "version": 1,
  "repositories": {
    "research-workspace": {
      "fetch_urls": [
        "https://github.com/example/research-workspace.git"
      ],
      "object_format": "sha1",
      "allowed_ref_patterns": [
        "refs/heads/*",
        "refs/tags/*"
      ],
      "local_path": "~/repos/research-workspace"
    }
  }
}
```

### 7.1 Registry requirements

- Repository IDs MUST match `^[a-z0-9][a-z0-9._-]{0,63}$`.
- A repository ID MUST identify one logical Git repository.
- `fetch_urls` MUST contain at least one operator-approved URL.
- Credentials MUST NOT be stored in the registry.
- `object_format` MUST be `sha1` or `sha256`.
- `allowed_ref_patterns` controls which advertised refs establish publication. Patterns are `fnmatch`-style over full advertised ref names (`refs/heads/main`, `refs/heads/release/*`). Default: `["refs/heads/*", "refs/tags/*"]`.
- `local_path` (OPTIONAL) points at a working clone or mirror. It is REQUIRED for `mint`; `verify` and `materialize` fall back to the publication cache when it is absent or lacks the commit.
- Registry changes MUST be version controlled.
- Implementations MUST reject an unknown repository ID.
- Implementations MUST reject a `local_path` repository whose object format conflicts with the registry (`REPOSITORY_MISMATCH`).

Mirrors MAY be listed as additional fetch URLs. They must represent the same Git object graph; verification succeeds if a trusted URL provides the required published commit and matching objects.

## 8. Canonical pin schema

The authoritative v1 representation is a JSON object:

```json
{
  "version": 1,
  "repository": "research-workspace",
  "commit_oid": "sha1:8b613fe09df32f8b8b78c01c764322d684f237ad",
  "path": "reports/result.md",
  "kind": "file",
  "mode": "100644",
  "object_oid": "sha1:6d82c8a4f74a1057801fd216c5de240e1cfb472f",
  "content_digest": "sha256:4c194f20c7f6f332580c2f858d3071a06274fb4f3f970872afcaccd4d7bf7a23"
}
```

### 8.1 Fields

| Field | Required | Semantics |
|---|---:|---|
| `version` | Yes | Integer schema version. Must equal `1`. |
| `repository` | Yes | Stable repository ID from the registry. |
| `commit_oid` | Yes | Algorithm-prefixed full commit OID. |
| `path` | Yes | Normalized repository-relative POSIX path. |
| `kind` | Yes | `file` or `tree`. |
| `mode` | Yes for file | `100644` or `100755`. Omitted for tree pins. |
| `object_oid` | Yes | Algorithm-prefixed full Git blob or tree OID at `commit_oid:path`. |
| `content_digest` | Yes | Algorithm-prefixed independent digest, always SHA-256 in v1. |

Unknown fields MUST cause strict consumers to reject a v1 pin. This prevents silently assigning meaning to data the consumer does not understand. Future optional data requires a new schema version or a separately versioned envelope.

### 8.2 Object identifier encoding

- Git OIDs MUST be encoded as `sha1:<40 lowercase hex>` or `sha256:<64 lowercase hex>`.
- The prefix MUST match the registered repository's object format.
- Abbreviated OIDs, uppercase hex, and unprefixed hashes MUST be rejected in canonical pins.
- `content_digest` MUST be `sha256:<64 lowercase hex>`.

### 8.3 Canonical JSON encoding

Canonical pin bytes are:

- UTF-8 without BOM;
- one JSON object with no insignificant whitespace;
- keys ordered lexicographically by Unicode code point;
- strings encoded using standard JSON escaping;
- terminated by one LF byte when stored as a standalone file.

The newline is a storage delimiter and is not included when hashing or embedding the canonical object in another record.

Example canonical line:

```json
{"commit_oid":"sha1:8b613fe09df32f8b8b78c01c764322d684f237ad","content_digest":"sha256:4c194f20c7f6f332580c2f858d3071a06274fb4f3f970872afcaccd4d7bf7a23","kind":"file","mode":"100644","object_oid":"sha1:6d82c8a4f74a1057801fd216c5de240e1cfb472f","path":"reports/result.md","repository":"research-workspace","version":1}
```

## 9. Display labels

Humans may be shown a non-authoritative shorthand:

```text
research-workspace:reports/result.md@8b613fe0
```

This label MUST NOT be stored in place of the canonical pin and MUST NOT be accepted by downstream services as an authoritative reference.

Because paths may contain `@` or `:`, display labels are not generally reversible. A UI that needs a reversible textual encoding SHOULD use base64url-encoded canonical JSON prefixed with `hivepin:v1:`.

## 10. Path rules

V1 paths:

- MUST be valid UTF-8 normalized to NFC;
- MUST use `/` as separator;
- MUST be relative to the repository root;
- MUST NOT begin with `/`;
- MUST NOT contain an empty component, `.` component, or `..` component;
- MUST NOT contain NUL, CR, LF, or backslash;
- MUST preserve case;
- MAY contain spaces, `@`, `:`, and other valid UTF-8 characters;
- use `.` only as the special path representing the whole repository tree.

The helper MUST compare paths as Git path bytes after normalization. It MUST NOT apply host-filesystem case folding.

## 11. Supported objects

### 11.1 Files

V1 supports regular Git blobs with mode `100644` or `100755`.

For a file:

- `object_oid` is the Git blob OID;
- `mode` is the mode recorded by the containing Git tree;
- `content_digest` is SHA-256 over the exact raw file bytes;
- no newline conversion, text decoding, or content normalization is permitted.

### 11.2 Trees

V1 supports Git trees containing regular files and subdirectories only.

The tree content digest is SHA-256 over a canonical manifest. The manifest contains one compact JSON object per regular file, followed by LF. Entries are ordered by the UTF-8 byte sequence of their normalized relative path.

Example:

```jsonl
{"digest":"sha256:abc...","mode":"100644","path":"README.md","size":812}
{"digest":"sha256:def...","mode":"100755","path":"scripts/run.sh","size":1432}
```

Manifest paths are relative to the pinned tree root. Empty directories are not represented because Git does not store them.

### 11.3 Rejected objects

V1 MUST reject:

- symlinks (`120000`);
- submodules/gitlinks (`160000`);
- malformed trees;
- paths whose materialized representation would collide after platform normalization;
- special filesystem objects.

The entire tree is rejected if any descendant is unsupported. Implementations must not silently omit unsupported entries.

## 12. Operations

### 12.1 `mint`

Library contract:

```text
mint(repository_id, path, commit=None, source_mode="worktree") -> Pin
```

CLI:

```text
hive-pin mint REPOSITORY PATH [--commit COMMIT] [--output PIN_FILE] [--json]
```

#### Default worktree mode

When `--commit` is omitted:

1. Resolve the registered repository and its local working copy.
2. Resolve `HEAD` to a full commit OID.
3. Normalize and validate the requested path.
4. Refuse if the selected file or tree differs from `HEAD`.
5. For a tree, also refuse if untracked files exist beneath it.
6. Confirm that the commit is published through an allowed remote ref.
7. Resolve the object kind, mode, and object OID from the commit tree.
8. Compute the independent content digest from committed bytes.
9. Emit the canonical pin.

This prevents a user from mistakenly believing that uncommitted working-tree content was pinned.

#### Explicit commit mode

When `--commit` is supplied:

- resolve it to a full commit OID;
- read content exclusively from that commit;
- do not compare it with the current working tree;
- require it to be published through an allowed remote ref;
- emit the canonical pin.

The CLI MAY accept an abbreviated commit or mutable ref as user input, but it MUST resolve it immediately and emit only the full immutable commit OID.

#### Output

- Without `--output`, stdout contains the canonical pin JSON only.
- With `--output`, the canonical pin is written atomically to that file and stdout contains the human display label and output path.
- With `--json`, stdout contains one machine-readable operation-result object instead of human output. When combined with `--output`, the result includes the output path; otherwise it embeds the canonical pin object.
- Diagnostics: stderr only.

### 12.2 `verify`

Library contract:

```text
verify(pin, publication="required") -> VerificationResult
```

CLI:

```text
hive-pin verify PIN_FILE [--offline] [--json]
```

Verification MUST check:

1. Schema and canonical field validity.
2. Repository registry membership and object-format agreement.
3. Commit existence and type.
4. Publication through an allowed ref, unless explicitly offline.
5. Path existence at the commit.
6. Object kind and file mode.
7. Git object OID.
8. Independently computed content digest.
9. Absence of unsupported tree entries.

`--offline` MAY use an existing trusted local cache but must return `publication_status: "not_checked"`. An offline result is not sufficient for admitting a new authoritative hive event.

Example result:

```json
{
  "status": "verified",
  "publication_status": "verified",
  "repository": "research-workspace",
  "commit_oid": "sha1:8b613fe09df32f8b8b78c01c764322d684f237ad",
  "path": "reports/result.md"
}
```

### 12.3 `materialize`

Library contract:

```text
materialize(pin, destination, publication="required") -> MaterializationResult
```

CLI:

```text
hive-pin materialize PIN_FILE DESTINATION [--offline] [--json]
```

Materialization MUST:

1. Perform all `verify` checks.
2. Require that `DESTINATION` does not exist.
3. Create a temporary directory beside the destination.
4. Extract content without using `git checkout` and without invoking hooks.
5. Preserve the repository-relative path below the temporary directory.
6. Preserve regular-file executable mode.
7. Reject traversal, symlinks, submodules, collisions, and unsupported objects.
8. Recompute and verify the materialized content digest.
9. Atomically rename the temporary directory to `DESTINATION` only after success.
10. Remove the temporary directory after any failure.

For a file pin at `reports/result.md` materialized to `/tmp/pin-123`, the resulting file is:

```text
/tmp/pin-123/reports/result.md
```

For a tree pin, the complete pinned subtree is preserved beneath the destination using its repository-relative root.

## 13. Publication verification

A commit is published when it is reachable from at least one ref that:

1. is advertised by a registered trusted fetch URL; and
2. matches an `allowed_ref_patterns` entry.

Implementations SHOULD fetch advertised allowed refs into an isolated cache namespace rather than modifying user branch or tag refs. A stale local `origin/main` is not proof of publication.

Remote unavailability is different from failed verification:

- If the remote is reachable and the commit is not reachable from an allowed ref, return `COMMIT_NOT_PUBLISHED`.
- If publication cannot be checked because all trusted remotes are unavailable, return `REMOTE_UNAVAILABLE`.

No authoritative pin may be minted on either result.

Publication verification establishes that a new authoritative pin was remotely obtainable when minted. It does not force a Git host to retain unreachable objects forever. Operators that require indefinite reconstruction must separately retain pinned objects through protected refs, repository retention policy, mirrors, or the archival mechanisms introduced by later releases.

## 14. API result requirements

All library operations return typed results or typed errors. CLI `--json` mode emits equivalent JSON.

Successful results MUST include:

- operation;
- status;
- repository;
- commit OID;
- path;
- publication status;
- canonical pin for `mint`;
- destination for `materialize`.

Failures MUST include:

- stable error code;
- short human-readable message;
- operation;
- safe contextual fields;
- no credentials, signed URLs, or secret-bearing command output.

## 15. Error codes and process exits

| Code | Meaning |
|---|---|
| `INVALID_PIN` | Invalid JSON, schema, field, or canonical representation. |
| `UNSUPPORTED_VERSION` | Pin or registry version is unsupported. |
| `INVALID_REGISTRY` | Registry or config file is missing, malformed, or has an unknown key. |
| `UNKNOWN_REPOSITORY` | Repository ID is absent from the registry. |
| `REPOSITORY_MISMATCH` | Local repository identity conflicts with the registry. |
| `OBJECT_FORMAT_MISMATCH` | OID algorithm conflicts with repository format. |
| `COMMIT_NOT_FOUND` | Commit does not exist in the resolved repository. |
| `COMMIT_NOT_PUBLISHED` | Commit is not reachable from an allowed remote ref. |
| `REMOTE_UNAVAILABLE` | Publication could not be checked. |
| `INVALID_PATH` | Path violates normalization or safety rules. |
| `PATH_NOT_FOUND` | Path does not exist at the commit. |
| `DIRTY_PATH` | Default mint path differs from committed content or contains untracked files. |
| `KIND_MISMATCH` | Actual Git object kind differs from the pin. |
| `UNSUPPORTED_OBJECT` | Symlink, submodule, or unsupported object encountered. |
| `MODE_MISMATCH` | File mode differs from the pin. |
| `OBJECT_MISMATCH` | Resolved Git object OID differs from the pin. |
| `CONTENT_MISMATCH` | Independently computed content digest differs from the pin. |
| `DESTINATION_EXISTS` | Materialization destination already exists. |
| `PATH_COLLISION` | Tree cannot be represented safely, or an entry would escape the destination. |
| `LIMIT_EXCEEDED` | A configured `max_file_bytes` / `max_tree_files` / `max_tree_bytes` limit was exceeded. |
| `INTERNAL_ERROR` | Unexpected implementation failure. |

Process exit codes:

- `0`: success;
- `2`: invalid invocation or malformed input — `INVALID_PIN`, `UNSUPPORTED_VERSION`, `INVALID_PATH`, `INVALID_REGISTRY`, `DESTINATION_EXISTS`;
- `3`: verification failure — every other non-`REMOTE_UNAVAILABLE`, non-`INTERNAL_ERROR` code;
- `4`: remote temporarily unavailable — `REMOTE_UNAVAILABLE`;
- `5`: local I/O or unexpected internal failure — `INTERNAL_ERROR`.

Callers MUST rely on the stable error code, not parse human-readable messages.

## 16. Security requirements

The implementation MUST:

- invoke Git without executing hooks;
- avoid shell interpolation of repository IDs, refs, paths, and destinations;
- use argument arrays for child processes;
- reject path traversal before filesystem access;
- prevent destination writes through pre-existing symlinks;
- use temporary directories with restrictive creation semantics;
- avoid exposing remote credentials in logs or results;
- apply explicit network and operation timeouts;
- cap maximum file count and total extracted bytes using operator-configurable limits;
- fail closed when identity or digest checks cannot be completed;
- leave the caller's index, branches, worktree, and configured remotes unchanged.

The implementation SHOULD use Git plumbing commands or a Git library rather than porcelain commands whose behavior depends on user configuration.

## 17. Configuration

The following operator-configurable limits are required:

```text
max_file_bytes
max_tree_files
max_tree_bytes
remote_timeout_seconds
materialization_timeout_seconds
cache_directory
repository_registry_path
```

Resolution order (later wins): built-in defaults → JSON config file (`$HIVEPIN_CONFIG` or `--config`) → environment (`HIVEPIN_MAX_FILE_BYTES`, `HIVEPIN_CACHE_DIRECTORY`, `HIVEPIN_REPOSITORY_REGISTRY_PATH`, …) → explicit call arguments.

v1.0 defaults: `max_file_bytes` 256 MiB, `max_tree_files` 50000, `max_tree_bytes` 2 GiB, `remote_timeout_seconds` 60, `materialization_timeout_seconds` 300, `cache_directory` `~/.cache/hivepin`, `repository_registry_path` `registry.json`.

Exceeding a limit raises `LIMIT_EXCEEDED` (exit `3`) with a contextual message.

## 18. Required implementation deliverables

The team must deliver:

1. `pin-v1.schema.json` — pin JSON Schema.
2. `repository-registry-v1.schema.json` — registry JSON Schema.
3. `hivepin` library with typed models and the three operations.
4. `hive-pin` CLI.
5. Canonical tree-manifest encoder.
6. Isolated repository cache and publication verifier.
7. Unit and integration test suites.
8. Security regression tests.
9. A short operator guide.
10. One external-hive adoption note describing setup, successful round trip, and any incompatibilities found.

Language and packaging are implementation choices, provided the CLI and canonical formats conform exactly.

## 19. Acceptance tests

R0 is complete only when all tests below pass in CI and on one external hive.

### 19.1 Determinism and round trip

- Minting the same file from two clean clones produces byte-identical canonical pins.
- Minting the same tree from two clean clones produces byte-identical canonical pins.
- `mint → verify → materialize → recompute` succeeds for files and trees.
- Executable mode is preserved.
- Binary files and files without trailing newlines round-trip exactly.

### 19.2 History semantics

- A path renamed after the pinned commit still materializes under its historical name.
- A path deleted after the pinned commit still materializes.
- Moving a branch or tag does not change an existing pin.
- Abbreviated or mutable input is resolved to a full commit OID before pin creation.

### 19.3 Worktree and publication

- Default mint refuses a modified tracked file.
- Default tree mint refuses relevant untracked files.
- Explicit-commit mint ignores unrelated working-tree changes.
- Mint refuses a local-only commit.
- Mint succeeds after the commit becomes reachable through an allowed advertised ref.
- Remote outage produces `REMOTE_UNAVAILABLE`, not `COMMIT_NOT_PUBLISHED`.

### 19.4 Parsing and path safety

- Paths containing spaces, `@`, `:`, and non-ASCII NFC characters round-trip.
- Absolute paths, `..`, empty components, backslashes, NUL, CR, and LF are rejected.
- Case-sensitive paths remain distinct.
- Materialization cannot write outside its destination.
- A pre-existing destination is never overwritten.

### 19.5 Unsupported objects

- A file symlink pin is rejected.
- A tree containing any symlink is rejected as a whole.
- A submodule pin is rejected.
- A tree containing a submodule is rejected as a whole.
- Platform-normalization collisions are detected before output is exposed.

### 19.6 Tampering

- Changing `repository`, `commit_oid`, `path`, `kind`, `mode`, `object_oid`, or `content_digest` causes verification failure.
- Replacing cached Git objects or materialized bytes is detected.
- A registry/repository identity mismatch is detected.
- No failed materialization leaves a partially visible destination.

### 19.7 Non-interference

- Mint, verify, and materialize do not change working-tree files, index state, branches, tags, remotes, or stash.
- Repository hooks are not executed.
- Credentials and credential-bearing URLs do not appear in stdout, stderr, or structured errors.

## 20. Definition of done

R0 is done when:

1. The schemas and behavior in this specification are implemented.
2. All acceptance tests pass.
3. The interface is versioned and documented.
4. At least one downstream prototype consumes canonical pins without custom parsing.
5. At least one external hive has used the released tool and produced an adoption note.
6. Any divergence from this specification is recorded as an explicit v1 amendment rather than an undocumented implementation choice.

## 21. Recommended work breakdown

This should remain a small release:

| Work item | Indicative effort |
|---|---:|
| Freeze schemas and fixtures | 0.5 day |
| Git resolution and minting | 0.5–1 day |
| Verification and publication checking | 0.5–1 day |
| Safe materialization and tree manifests | 0.5–1 day |
| CLI, errors, and documentation | 0.5 day |
| Conformance and security tests | 1–2 days |
| External adoption and fixes | 0.5–1 day |

Expected total: approximately 4–7 developer-days, depending primarily on cross-platform support and remote-publication edge cases.

## 22. Future extensions

The following require a later version and must not be improvised inside v1:

- symlink semantics;
- recursive submodule pins;
- signed pin envelopes;
- content-addressed artifact-store integration;
- cross-repository manifests;
- alternative independent digest algorithms;
- provenance and review receipts;
- migration or alias records connecting renamed repository IDs.
