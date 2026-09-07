# Changelog

All notable changes to `hivepin`. The **pin schema**, **registry schema**, and
**tree-manifest encoding** are frozen contracts — a change to any of them is a
new schema version, never a point release.

## [1.0.0] — 2026-09-08

First release. Implements [`SPEC.md`](SPEC.md) v1.0 in full.

- Canonical pin (JSON, code-point-ordered keys, no insignificant whitespace) with
  `version, repository, commit_oid, path, kind, mode?, object_oid, content_digest`.
- Repository registry (v1) with `fetch_urls`, `object_format`, `allowed_ref_patterns`,
  optional `local_path`.
- `mint` — worktree (clean-HEAD) and explicit-commit modes; full-OID resolution;
  publication check; symlink/submodule/limit rejection.
- `verify` — schema, registry, commit, kind/mode/OID, independent content digest,
  publication (`--offline` → `publication_status: "not_checked"`).
- `materialize` — extraction via `git cat-file` only (no checkout, no hooks),
  traversal/symlink/collision guards, digest re-check, atomic rename, cleanup on
  failure.
- `hive-pin` CLI with `--json`, stable error codes, spec'd exit codes.
- `schemas/pin-v1.schema.json`, `schemas/repository-registry-v1.schema.json`.
- 116-test suite covering determinism, history semantics, path safety,
  unsupported objects, tampering, and non-interference (SPEC §19).
