# hivepin

**One Hive R0 — immutable git pinning.**

A *pin* is a durable, unambiguous reference to an exact git commit in a
registered repository, optionally narrowed to a file or directory in it:

```
mtg-player@8b613fe0                →  {"commit_oid":"sha1:8b61…","repository":"mtg-player","version":2}
workspace:reports/result.md@9c1e…  →  {"commit_oid":"sha1:9c1e…","path":"reports/result.md","repository":"workspace","version":2}
```

Given the same registered repository and commit, two conforming implementations
produce **byte-identical** pins. A pin keeps identifying the same bytes regardless
of later edits, renames, branch movement, or which machine resolves it.

Pins are **format v2** ([`SPEC-v2.md`](SPEC-v2.md); one-page overview in
[`docs/pin-v2.md`](docs/pin-v2.md)): the commit is the unit, integrity is the git
hash chain re-computed by hivepin, and symlinks are materialized safely. Format
v1 ([`SPEC.md`](SPEC.md): file and tree pins with an independent SHA-256 digest)
is still verified and materialized, and can still be minted with `--format 1`.

`hivepin` is the reference implementation: a small Python library and the
`hive-pin` CLI, with three operations — **mint**, **verify**, **materialize** —
plus the formats in [`SPEC.md`](SPEC.md), [`SPEC-v2.md`](SPEC-v2.md) and
[`schemas/`](schemas/).

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
pip install "git+https://github.com/1-hive/hive-pin"

# or as an isolated tool
uv tool install "git+https://github.com/1-hive/hive-pin"
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

**2. Mint a pin** for a committed, pushed commit or file:

```bash
export HIVEPIN_REPOSITORY_REGISTRY_PATH=$PWD/registry.json

hive-pin mint workspace                       # the whole HEAD commit (worktree must be clean)
# → {"commit_oid":"sha1:…","repository":"workspace","version":2}

hive-pin mint workspace projects/mtg-player/orders/2026-09-07-thing.md
# → {"commit_oid":"sha1:…","path":"projects/mtg-player/orders/2026-09-07-thing.md","repository":"workspace","version":2}

hive-pin --json mint workspace projects/mtg-player/orders/2026-09-07-thing.md --output thing.pin
hive-pin mint code --commit 3f2a9c1                # a specific pushed commit
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

Directory pins work the same way (`hive-pin mint code src`). A whole-commit pin
materializes the repository root. Symlinks that resolve inside the materialized
tree are created; any other symlink, and every submodule, is left out and listed
in the result's `omitted` field (`--json`).

## Library

```python
from pathlib import Path
from hivepin import Registry, Config, mint, verify, materialize, parse_pin, PinError

reg = Registry.load(Path("registry.json"))
cfg = Config.load()

res = mint("workspace", "reports/result.md", reg, config=cfg)   # None: the whole commit
pin_text = res.pin.envelope                      # hivepin:v2:<base64url> — reversible
canonical = res.pin.to_canonical_bytes()         # the bytes to embed in an event

pin = parse_pin(pin_text)                         # v1 or v2
verify(pin, reg, config=cfg)                      # raises PinError on any mismatch
out = materialize(pin, "/tmp/out", reg, config=cfg)
assert out.omitted == ()                          # nothing left out
```

## How a pin is resolved

For a v2 pin:

| Step | mint | verify | materialize |
|---|---|---|---|
| registry lookup (repo id → URLs, format, allowed refs) | ✓ | ✓ | ✓ |
| commit resolved to a **full** OID | ✓ | — (already full) | — |
| worktree clean & matches HEAD (default mode) | ✓ | | |
| commit, trees and object down to the path re-hashed | ✓ | ✓ | ✓ |
| every object written re-hashed | | | ✓ |
| path is a file or directory (not a symlink or submodule) | ✓ | ✓ | ✓ |
| symlinks created only if they resolve inside; submodules listed in `omitted` | | | ✓ |
| commit reachable from an allowed published ref | ✓ *(unless `--offline`)* | ✓ *(unless `--offline`)* | ✓ *(unless `--offline`)* |
| safe extraction (no checkout, no hooks, no traversal) | | | ✓ |

v1 pins are checked as in [`SPEC.md`](SPEC.md) §12: kind, mode, object OID and the
independent SHA-256 digest, with symlinks and submodules rejected.

`--offline` sets `publication_status: "not_checked"`; an offline result is not
sufficient to admit a new authoritative hive event.

## Error codes

Every failure is a stable code (see [`SPEC.md` §15](SPEC.md)); the CLI exit code
is derived from it — `0` ok, `2` bad input, `3` verification failure, `4` remote
unavailable, `5` internal. Callers must branch on the code, never the message.

## For other hives

The portable contract is [`SPEC-v2.md`](SPEC-v2.md) on top of [`SPEC.md`](SPEC.md),
plus [`schemas/pin-v2.schema.json`](schemas/pin-v2.schema.json),
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
