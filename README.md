# hivepin

**One Hive R0 — immutable git pinning.**

A *pin* is a durable, unambiguous reference to a file or directory at an exact git
commit:

```
sha1:<repo>:<path>@<commit>   →   { "version": 1, "repository": "...", "commit_oid": "sha1:…", … }
```

Given the same registered repository and content, two conforming implementations
produce **byte-identical** pins. A pin keeps identifying the same bytes regardless
of later edits, renames, branch movement, or which machine resolves it.

`hivepin` is the reference implementation: a small Python library and the
`hive-pin` CLI, with three operations — **mint**, **verify**, **materialize** —
plus the frozen formats in [`SPEC.md`](SPEC.md) and [`schemas/`](schemas/).

- No third-party dependencies. Python ≥ 3.10 and `git` on `PATH`.
- git is invoked through plumbing only: no hooks, no checkout, your index /
  worktree / branches / stash are never touched.
- Network fetches land in a separate cache repo, never your clone's refs.

This is R0 of the One Hive incremental release plan. It does **not** implement the
event record, reviews, authorization, agent identity, or artifact promotion —
those are later releases that consume pins.

---

## Install

```bash
# from a checkout
pip install .

# or straight from git
pip install "git+https://github.com/one-hive/hivepin"

# or as an isolated tool
uv tool install "git+https://github.com/one-hive/hivepin"
```

Zero-install also works — the package is importable from `src/` and the CLI is
`python -m hivepin.cli`.

## Quick start

**1. Write a repository registry** (`registry.json`) — this is per-hive config,
not shipped with the tool:

```json
{
  "version": 1,
  "repositories": {
    "workspace": {
      "fetch_urls": ["https://github.com/your-org/hive-workspace.git"],
      "local_path": "~/repos/hive-workspace",
      "allowed_ref_patterns": ["refs/heads/main"]
    }
  }
}
```

**2. Mint a pin** for a committed, pushed file:

```bash
export HIVEPIN_REPOSITORY_REGISTRY_PATH=$PWD/registry.json

hive-pin mint workspace projects/mtg-player/orders/2026-09-07-thing.md
# → {"commit_oid":"sha1:…","content_digest":"sha256:…","kind":"file", … ,"version":1}

hive-pin --json mint workspace projects/mtg-player/orders/2026-09-07-thing.md --output thing.pin
```

**3. Verify** it still resolves to the same content (and is still published):

```bash
hive-pin verify "$(cat thing.pin)"
# → verified   workspace:projects/mtg-player/…@a1b2c3d4   publication=verified

hive-pin verify "$(cat thing.pin)" --offline   # skip the network check
```

**4. Materialize** the pinned content into a fresh directory:

```bash
hive-pin materialize "$(cat thing.pin)" /tmp/checkout
# → /tmp/checkout
cat /tmp/checkout/projects/mtg-player/orders/2026-09-07-thing.md
```

Directory pins work the same way — `hive-pin mint code src/ ` or `hive-pin mint
code .` for the whole tree.

## Library

```python
from pathlib import Path
from hivepin import Registry, Config, mint, verify, materialize, Pin, PinError

reg = Registry.load(Path("registry.json"))
cfg = Config.load()

res = mint("workspace", "reports/result.md", reg, config=cfg)
pin_text = res.pin.envelope                      # hivepin:v1:<base64url> — reversible
canonical = res.pin.to_canonical_bytes()         # the bytes to embed in an event

pin = Pin.parse(pin_text)
verify(pin, reg, config=cfg)                      # raises PinError on any mismatch
materialize(pin, "/tmp/out", reg, config=cfg)
```

## How a pin is resolved

| Step | mint | verify | materialize |
|---|---|---|---|
| registry lookup (repo id → URLs, format, allowed refs) | ✓ | ✓ | ✓ |
| commit resolved to a **full** OID | ✓ | — (already full) | — |
| worktree clean & matches HEAD (default mode) | ✓ | | |
| object kind / mode / OID at `commit:path` | ✓ | ✓ | ✓ |
| independent SHA-256 content digest | ✓ (recorded) | ✓ (checked) | ✓ (re-checked after write) |
| symlinks / submodules rejected | ✓ | ✓ | ✓ |
| commit reachable from an allowed published ref | ✓ *(unless `--offline`)* | ✓ *(unless `--offline`)* | ✓ *(unless `--offline`)* |
| safe extraction (no checkout, no hooks, no traversal) | | | ✓ |

`--offline` sets `publication_status: "not_checked"`; an offline result is not
sufficient to admit a new authoritative hive event.

## Error codes

Every failure is a stable code (see [`SPEC.md` §15](SPEC.md)); the CLI exit code
is derived from it — `0` ok, `2` bad input, `3` verification failure, `4` remote
unavailable, `5` internal. Callers must branch on the code, never the message.

## For other hives

The portable contract is [`SPEC.md`](SPEC.md) plus
[`schemas/pin-v1.schema.json`](schemas/pin-v1.schema.json) and
[`schemas/repository-registry-v1.schema.json`](schemas/repository-registry-v1.schema.json).
`hivepin` is a reference implementation — adopt it directly (`pip` / `uv tool`),
vendor the single package, shell out to `hive-pin --json`, or reimplement to the
schemas. The registry file stays yours; the formats do not change without a
version bump.

## Development

```bash
uv venv && uv pip install -e ".[test]"
uv run pytest
```

## License

GPL-3.0-or-later. See [`LICENSE`](LICENSE).
