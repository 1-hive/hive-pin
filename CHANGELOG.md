# Changelog

All notable changes to `hivepin`. The **pin schema**, **registry schema**, and
**tree-manifest encoding** are frozen contracts — a change to any of them is a
new schema version, never a point release.

## [2.0.0] — 2026-10-03

Pin format v2 ([`SPEC-v2.md`](SPEC-v2.md), overview [`docs/pin-v2.md`](docs/pin-v2.md)).
Based on Mike's proposal "What anything is, is a commit".

- **v2 pin** `{version: 2, repository, commit_oid, path?}`: the commit is the unit;
  a path, when present, is binding for consumers. `schemas/pin-v2.schema.json`.
- **Integrity by re-hashing git objects**: verify re-hashes the commit, the trees
  down to the path and the object at it; materialize re-hashes every object it
  writes. No independent digest in v2 pins.
- **Safe symlinks**: created last, only when they resolve inside the materialized
  root (in-tree resolution, up to 40 hops). Other symlinks and all submodules are
  listed in the new `omitted` result field. `.git` tree entries are refused.
- **`mint` emits v2 by default** (breaking): `hive-pin mint REPO [PATH]`, where no
  path (or `.`) pins the whole commit. `--format 1` / `version=1` mints v1.
- `verify` and `materialize` accept v1 and v2; v1 pins keep their v1 semantics.
- Library: `PinV2`, `parse_pin`, `pin_from_dict`, `AnyPin`, and the `…V2` result
  types. `Pin` is still the v1 pin.
- 34 new tests (150 in all).

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
