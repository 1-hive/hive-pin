# One Hive R0: pin format v2

**Status:** implemented by [`hivepin`](README.md) 2.0.
**Origin:** Mike's proposal "What anything is, is a commit" (2026-10-02), with three additions: a binding `path`, a pinned review base, and safe symlinks.
**Scope:** the changes to [`SPEC.md`](SPEC.md) (R0 v1.0). Sections not mentioned here are unchanged and apply to v2 pins too. §8 says what follows for hive-record and the 1-hive deployment. A one-page overview is in [`docs/pin-v2.md`](docs/pin-v2.md).

## 0. Why

In v1, a result can pin one file while the commit holds other changes that no pin covers. In 1-hive today, a result pins only its report. The code is named in the report's text, and the reviewer reads a branch that can still move, so the record doesn't fix which code was reviewed.

v2 makes the commit the unit. A pin names a commit in a registered repository, and optionally a part of it. Everything else in a v1 pin follows from those two values or from git, except the independent SHA-256 digest. v2 replaces that digest by re-hashing git objects (§4).

## 1. The pin

```json
{"commit_oid":"sha1:8b613fe09df32f8b8b78c01c764322d684f237ad","repository":"mtg-player","version":2}
{"commit_oid":"sha1:9c1e…","path":"projects/mtg-player/reports/col-3-result.md","repository":"workspace","version":2}
```

| Field | Required | Semantics |
|---|---:|---|
| `version` | yes | `2`. |
| `repository` | yes | Registry ID, as in v1. |
| `commit_oid` | yes | Algorithm-prefixed full commit OID, as in v1. |
| `path` | no | A file or directory in that commit. Absent: the whole commit. |

**Removed from v1:** `kind`, `mode`, `object_oid` (all follow from `commit_oid` + `path`) and `content_digest` (§4).

**`path` is binding.** The commit is what the pin certifies. The path is what the referring event is about, and consumers MUST use it. A gateway loading a policy pin loads exactly that directory. A reviewer reading a `report` ref reads exactly that file. A pin without a path refers to the whole commit.

Path rules: the same as v1 §10, except that `.` is no longer allowed. The whole commit is written by leaving `path` out, so each pinned thing has exactly one spelling. The path MUST resolve to a regular file (`100644`/`100755`) or a tree. A path that is itself a symlink or a submodule is `UNSUPPORTED_OBJECT`.

Canonical encoding, strict consumers (unknown fields rejected), and the rule "no mutable refs" are unchanged.

## 2. Display labels

`mtg-player@8b613fe0` (whole commit) and `workspace:projects/…/col-3-result.md@9c1e2d4a` (with a path). They are non-authoritative, as in v1. The reversible encoding is `hivepin:v2:<base64url(canonical-json)>`.

## 3. Operations

### mint

```text
hive-pin mint REPOSITORY [PATH] [--commit COMMIT] [--format 1|2] [--output PIN_FILE] [--json]
```

- Without `PATH`: a whole-commit pin. On input, `.` also means the whole commit; the pin then has no path.
- `--format 1` mints a v1 pin (`SPEC.md`), which needs a path. The default is 2.
- The result names the `kind` of the pinned object: `commit`, `tree` or `file`. It is informative and not part of the pin.
- Worktree mode (no `--commit`): refuses with `DIRTY_PATH` if the pinned scope differs from `HEAD`. For a whole-commit pin, that means any modified tracked file or any untracked, non-ignored file anywhere in the repository. With a path, the v1 rules apply.
- Publication check: unchanged (v1 §13). A pin can't be minted before the commit is pushed.

### verify

It checks, in order:
1. the schema, the registry and the object format (v1 checks 1–2);
2. that the commit exists and its type;
3. publication, unless offline;
4. that the path exists and is a file or tree, when a path is given;
5. **object integrity along the path:** recompute the hash of the commit object, of each tree object from the root to the path, and of the object at the path, and compare each with the OID it is stored under (`OBJECT_MISMATCH`).

Verify does not walk the pinned tree below the path, so a whole-commit pin verifies in a few object reads and admitting a ref stays cheap. Its result names the `kind`, as mint's does.

### materialize

Unchanged from v1 except for these points:
- A whole-commit pin extracts the repository root into the destination. A path pin extracts that path, keeping its repository-relative location (as in v1).
- Every blob and tree written is re-hashed and compared with its tree entry (§4). On any mismatch the result is `OBJECT_MISMATCH` and the output is never exposed.
- Symlinks and submodules follow §5.
- A tree entry named `.git` (in any case) is refused with `INVALID_PATH`, as are entry names that are empty, `.`, `..`, contain `/`, NUL, CR, LF or a backslash, or are not UTF-8. Names are NFC-normalized; two names that collide after normalization are `PATH_COLLISION`.
- Limits (`max_tree_files`, `max_tree_bytes`) apply as in v1. Operators may need to raise them for whole-commit pins.

The result adds `omitted: [{path, kind, reason}]`, listing every entry not created (§5). An empty list means the whole scope was materialized.

### is_ancestor

```text
is_ancestor(ancestor, descendant, registry, offline=False) -> bool
```

Library only. Both must be v2 pins of the same repository (else `INVALID_PIN` or `REPOSITORY_MISMATCH`). Both are verified first, then git decides whether the first commit is an ancestor of, or equal to, the second. A consumer uses it to check that a change starts where it says it does, e.g. a `base` before its `code` (§8).

## 4. Integrity without `content_digest`

v1 keeps an independent SHA-256 digest because git objects are named by SHA-1. In v2, integrity is the git hash chain instead: commit → tree → blobs, recomputed by hivepin itself and never trusted from a cache or `local_path`. A tampered object in a cache or clone is detected, as in v1 §19.6.

**What remains is a SHA-1 collision:** an author prepares two colliding versions, one reviewed and the other swapped in later.
- Doing that requires a chosen-prefix collision, which costs tens of thousands of dollars of compute.
- The forger would have to be the author, i.e. one of the hive's own agents.
- Git (since 2.13) and the major hosts use hardened SHA-1, which rejects the known collision patterns. Objects reach a clone or the publication cache through git, so that detection applies when they arrive; hivepin's own re-hash then catches anything changed afterwards.

A hive that needs more registers its repositories with `object_format: "sha256"`, which v1 already supports.

## 5. Symlinks and submodules

With whole-commit pins, rejecting any tree that contains a symlink would make most real repositories unpinnable. v2 materializes symlinks safely instead.

- **Symlinks (`120000`)** are created **last**, after every regular file and directory, so no write ever goes through a link. A link is created only if it is relative and resolves inside the materialized root. Resolution walks the target component by component inside the pinned tree, following in-tree links up to 40 hops. A lexical check isn't enough: with `d/l -> ..`, the target `d/l/../x` looks inside but leaves the root. Any other link (absolute, escaping, too many hops, or dangling outside the tree) is not created and is listed in `omitted` with reason `symlink_target`.
- **Submodules (`160000`)** are never fetched. They are listed in `omitted` with reason `submodule` and the gitlink's commit OID.
- **Other unsupported entries** and post-normalization path collisions still refuse the whole materialization, as in v1 (`UNSUPPORTED_OBJECT`, `PATH_COLLISION`).

Omitting is never silent: `omitted` is part of the result, and a consumer that needs a complete tree MUST check that it is empty.

## 6. Error codes

- **Removed** (v2 pins have no such field): `KIND_MISMATCH`, `MODE_MISMATCH`, `CONTENT_MISMATCH`. v1 pins still use them.
- **Changed:** `OBJECT_MISMATCH` now also covers a recomputed object hash that differs from its stored OID.
- Exit codes are unchanged.

## 7. Compatibility

- `verify` and `materialize` accept v1 and v2 pins. v1 pins keep exactly their v1 semantics, and old records stay verifiable.
- `mint` emits v2. `--format 1` (library: `version=1`) stays available for consumers that haven't moved yet.
- Library: `PinV2` is the v2 pin; `parse_pin` and `pin_from_dict` read either version; `is_ancestor` compares two v2 pins. `Pin` remains the v1 pin.
- The registry is unchanged (registry v1).
- Deliverables: `pin-v2.schema.json`, the encoder and checks above, and tests (§9).

## 8. Knock-on changes

### hive-record (amendment A3)

- **§8 pin admission:** step 2 validates against `pin-v1` **or** `pin-v2`. Step 3 is unchanged (`hivepin.verify`, with publication).
- **§10 policy:** a v2 policy pin MUST carry `path` (the policy tree). A whole-commit pin is refused as a policy ref, with `PIN_INVALID` and `detail.reason: "policy_needs_path"`.
- **Refs** stay capped at 16. One code pin and one base pin per touched repository fits easily.

### 1-hive profile

- **New rels:** `code` (the branch tip, a whole-commit pin) and `base` (the commit the branch started from, a whole-commit pin of the same repository).
- **`task.created`:** adds an optional `repos` list in `data.ext`: the registered repositories the task may change. A research-only task leaves it empty.
- **`task.result_posted`:** `result` stays required. The new condition `code_matches_repos` requires exactly one `code` and one `base` for each repository in the task's `repos`, and no others. Each `base` must be of the same repository as its `code` and an ancestor of it; that check uses the registry's cache. Refusal codes: `CODE_MISSING` (a listed repository has no pair), `CODE_OUT_OF_SCOPE` (a `code` for an unlisted repository), and `CODE_BASE_INVALID`. This turns the "stay inside the order's scope" stop-line into a rule the gateway enforces for code.

### 1-hive deployment

- **launch-task.sh:** records the base commit when it creates `hive/<task-id>`, and passes it in the kickoff.
- **Worker contract, step 5:** mint `code` (`hive-pin mint <repo> --commit <tip>`) and `base` (`hive-pin mint <repo> --commit <base>`), and post them with the result.
- **The chief of staff** sets `repos` on each order.
- **Reviewers** review `base..code` from the record, not the branch. If materialize reports `omitted` entries, the review lists them, and it fails if the change touches any of them. On a restart after a failed review, the new result carries the same base and a new tip, so a reviewer can read the whole change (`base..new`) or just the fix (`old..new`).
- **Merging** after acceptance merges the reviewed `code` commit by OID, not the branch head.

## 9. Acceptance tests (added or changed)

- Determinism: the same repository and commit give byte-identical whole-commit pins from two clones.
- A stray change outside the claimed files is visible in `base..code`. (This is the completeness case: no file list exists to leave it out of.)
- Tampering: a modified blob, tree or commit object in the cache or in `local_path` is detected (`OBJECT_MISMATCH`) by verify along the path and by materialize everywhere.
- Symlinks:
  - an in-tree relative link is created;
  - absolute and escaping links are omitted and listed;
  - the `d/l -> ..` case is caught;
  - no write goes through a link;
  - a path that is itself a symlink is refused.
- Submodules are omitted and listed with their OID, and the rest of the tree is materialized.
- v1 pins still verify and materialize with v1 semantics, and the v1 fixtures pass unchanged.
- Policy: a whole-commit policy pin is refused, and a path pin is accepted.
- 1-hive: a result is refused when a repository in `repos` has no `code`/`base` pair, when it carries `code` for an unlisted repository, or when a `base` is of another repository or not an ancestor. A research task with no `repos` posts a result with no `code`.

## 10. Decisions on earlier open questions

1. **`code` is required only for the repos the order names** (`repos`, §8). Research-only tasks name none and are never refused. Making `code` always optional would keep the gap v2 closes.
2. **`omitted` is reported, not refused.** hivepin lists the entries; the 1-hive reviewer judges them (§8). Refusing would make every repository with a submodule unreviewable.
3. **Workspace refs stay file or directory pins.** The workspace is shared by all projects, so a whole-commit pin there would mean everything, while the event is about one report. The pin still names the worker's own commit, so a reviewer can inspect that commit's whole diff when in doubt. Revisit if workers are seen editing other projects' files.
4. **Workspace changes carry no `base`.** Reports are read whole, not reviewed as diffs. A base is needed only where review means reading a diff, which is code.

These choices sit in the 1-hive profile and contract, not in the protocol, so other hives can decide differently.
