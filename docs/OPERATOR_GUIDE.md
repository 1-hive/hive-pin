# Operator guide

For a hive operator standing `hivepin` up, and for another hive adopting it.

## 1. Install

```bash
uv tool install "git+https://github.com/1-hive/hive-pin"        # isolated CLI
# or
pip install "git+https://github.com/1-hive/hive-pin"            # into an env
```

Runtime needs: Python ≥ 3.10, `git` on `PATH`, `tar` is **not** used. No other
dependencies.

Pin the tool itself for reproducibility — install a tag or a commit, and record
which one in your hive's deployment notes. (`hivepin` is R0; once your hive has a
record, pin `hivepin` with `hivepin`.)

## 2. The registry (`registry.json`)

Per-hive config, version-controlled in your hive's workspace repo. One entry per
repository that events will pin into:

```json
{
  "version": 1,
  "repositories": {
    "workspace": {
      "fetch_urls": ["https://github.com/your-org/hive-workspace.git"],
      "local_path": "~/hive/workspace",
      "allowed_ref_patterns": ["refs/heads/main"]
    },
    "mtg-player": {
      "fetch_urls": [
        "git@github.com:your-org/mtg-player.git",
        "https://github.com/your-org/mtg-player.git"
      ],
      "local_path": "~/hive/repos/mtg-player",
      "allowed_ref_patterns": ["refs/heads/main", "refs/tags/v*"]
    }
  }
}
```

- **`fetch_urls`** — the *canonical* remotes for the repo, in preference order.
  Never a fork, never an agent's personal remote. No `user:pass@` in the URL.
- **`allowed_ref_patterns`** — a commit counts as *published* only if it is
  reachable from a ref matching one of these. Keep this tight: `refs/heads/main`,
  release branches, release tags. An agent's feature branch being pushed is not
  publication.
- **`local_path`** — a working clone or mirror. Required for `mint`. `verify` and
  `materialize` can run without it (from the publication cache), which is the
  common case on a machine that didn't do the work.
- **`object_format`** — `sha1` (default) or `sha256`. Must match the real repo.

Point the tools at it:

```bash
export HIVEPIN_REPOSITORY_REGISTRY_PATH=/path/to/registry.json
# or pass --registry on every call
```

## 3. The publication cache

`hivepin` fetches published refs into `<cache_directory>/<repo-id>.git`
(default `~/.cache/hivepin`), under `refs/hivepin/*`. It never touches your
clone's branches, tags, `FETCH_HEAD`, or stash.

- Set `HIVEPIN_CACHE_DIRECTORY` to move it (e.g. onto the same filesystem as your
  workspace for fast materialize).
- Safe to delete at any time; it refills on the next online operation.
- On an air-gapped machine, prime it once online, then use `--offline`
  everywhere. Offline results carry `publication_status: "not_checked"` and are
  **not** sufficient to admit a new authoritative hive event.

## 4. Typical flows

**A worker pinning a result** (has a local clone, has pushed):

```bash
hive-pin --json mint mtg-player --commit "$TIP" --output code.pin      # the whole commit
hive-pin --json mint workspace reports/result.md --output result.pin  # one file
# embed the canonical object (the .pin file without its trailing LF) in the event
```

**A reviewer / gateway checking a pin from an event** (no local clone needed):

```bash
hive-pin verify "$(printf '%s' "$event_ref")"      # exit 0, or a code + exit 3/4
```

**Replaying an old task** (materialize the exact inputs):

```bash
hive-pin materialize "$pin" ./task-1234-inputs
```

With `--json`, the result's `omitted` lists every symlink and submodule that was
not created (SPEC-v2 §5). A consumer that needs the complete tree checks that it
is empty.

## 5. CI / conformance

```bash
git clone https://github.com/1-hive/hive-pin && cd hive-pin
uv venv && uv pip install -e ".[test]"
uv run python -m pytest  # 151 tests: v1 (SPEC §19) and v2 (SPEC-v2 §9)
```

If you reimplement `hivepin` for another language, run your implementation
against `SPEC.md` §19 and `SPEC-v2.md` §9, and check byte-identical pins against
this one for a shared fixture repo.

## 6. Non-Python hives

The portable contract is `SPEC.md` + `schemas/`. Options, cheapest first:

1. Shell out to `hive-pin --json` and read the result object.
2. Vendor `src/hivepin/` (pure stdlib) and call it from a small subprocess shim.
3. Reimplement to `schemas/pin-v2.schema.json`, `schemas/pin-v1.schema.json`,
   `schemas/repository-registry-v1.schema.json`, `SPEC-v2.md`, and `SPEC.md`
   §8/§11 for v1's canonical bytes and manifest.

The registry file format and the pin bytes of each pin version never change; a
new format is a new pin version. `hivepin` 2.x mints v2 and reads v1 and v2.
