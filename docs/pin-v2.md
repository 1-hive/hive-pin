# Pins v2 — overview

A **pin** is a permanent reference to work stored in git. Hive record events carry pins instead of content, so the record says exactly which work an order, report, result or review refers to.

## The pin

```json
{"commit_oid":"sha1:8b613fe0…","repository":"mtg-player","version":2}
{"commit_oid":"sha1:9c1e2d4a…","path":"projects/mtg-player/reports/col-3-result.md","repository":"workspace","version":2}
```

- **`repository`:** an ID from the hive's registry, not a URL. The operator-controlled registry maps it to fetch URLs, so repositories can move hosts without invalidating history, and pins never carry credentials.
- **`commit_oid`:** the full commit ID. It is never a branch or tag, since those move.
- **`path`** (optional) names the file or directory the event is about, and consumers must use it. Without a path, the pin refers to the whole commit.

A pin can only be minted for a commit that has been **pushed**: it must be reachable from an allowed branch or tag on a registered remote.

Humans see a short label, e.g. `mtg-player@8b613fe0` or `workspace:…/col-3-result.md@9c1e2d4a`. It's for display only, never stored in place of the pin.

## Operations

- **mint:** creates a pin from a pushed commit, optionally with a path.
- **verify:** checks that the pin is well formed, the repository is registered, the commit is published and the path exists. It also re-hashes the git objects from the commit down to the path. This is cheap even for a whole repository.
- **materialize:** extracts the pinned content into a new directory, with no checkout and no hooks. It re-hashes every object it writes, and exposes the result only if everything matches.

## Integrity

Every git object is named by the hash of its content: a commit names its tree, and a tree names its files. hivepin recomputes these hashes itself and never trusts a cache or a local clone, so any tampered object is detected.

The remaining risk is a forged SHA-1 collision. That would require an author to prepare two colliding versions in advance at significant cost, and git's hardened SHA-1 rejects the known attacks. A hive that wants more registers its repositories as SHA-256 git repositories.

## Symlinks and submodules

- **Symlinks** are created only if they resolve inside the extracted tree. They are written last, so no file is ever written through a link.
- **Submodules** are not fetched.

Anything not extracted is listed in the result's `omitted` field, never dropped silently.

## Pins in a task (1-hive)

1. **Order:** the chief of staff creates the task with its order pinned, and lists the repositories the task may change (`repos`).
2. **Branch:** the launcher creates `hive/<task-id>` from the current main and records that commit as the task's **base**.
3. **Work:** the worker commits and pushes code to the branch, and writes its report in the workspace.
4. **Result:** the result carries:
   - `result`: a file pin of the report;
   - for each repository in `repos`, `code` (a whole-commit pin of the branch tip) and `base` (a whole-commit pin of where the branch started).

   The gateway refuses the result if a listed repository has no pair, if a `code` names an unlisted repository, or if a `base` isn't an ancestor of its `code`. A research-only task lists no repositories and carries no `code`.
5. **Review:** the reviewer reads `base..code` from the record, which is exactly the task's change, including any stray edit. The reviewer lists any `omitted` entries, and fails the review if the change touches them. After a failed review, the next result keeps the same base with a new tip, so the record can show the whole change or just the fix.
6. **Merge:** after acceptance, the reviewed `code` commit is merged by its ID, not the branch head.

Workspace refs (orders, reports, reviews) stay file pins. The workspace is shared by all projects, and each event is about one document.

## Where things live

- **hive-pin (the protocol):** the pin format, the registry, and mint, verify and materialize.
- **hive-record:** checks every pin before admitting an event. A policy pin must carry a path, so the gateway loads exactly the policy tree.
- **The 1-hive profile and contract:** `repos`, `code` and `base`, and the review rules.

The 1-hive choices aren't part of the protocol, so other hives can set their own.
