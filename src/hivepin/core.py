# SPDX-License-Identifier: GPL-3.0-or-later
"""The three operations: mint, verify, materialize (SPEC.md section 12)."""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import gitio
from .canonical import (
    UNSUPPORTED_MODES,
    WHOLE_TREE,
    check_tree_relpath,
    manifest_digest,
    sha256_hex,
)
from .config import Config
from .errors import PinError
from .gitio import TreeEntry
from .pin import Pin, PinV2
from .registry import Registry, Repository

_FILE_MODES = ("100644", "100755")


# --------------------------------------------------------------------------- #
# results
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class MintResult:
    pin: Pin
    publication_status: str          # "verified" | "not_checked"

    def to_dict(self) -> dict:
        return {
            "status": "minted",
            "operation": "mint",
            "pin": self.pin.envelope,
            "canonical": self.pin.to_canonical_dict(),
            "label": self.pin.display_label,
            "repository": self.pin.repository,
            "commit_oid": self.pin.commit_oid,
            "path": self.pin.path,
            "kind": self.pin.kind,
            "publication_status": self.publication_status,
        }


@dataclass(frozen=True)
class VerificationResult:
    pin: Pin
    publication_status: str          # "verified" | "not_checked"
    _source: Path
    _entry: TreeEntry

    def to_dict(self) -> dict:
        return {
            "status": "verified",
            "operation": "verify",
            "pin": self.pin.envelope,
            "repository": self.pin.repository,
            "commit_oid": self.pin.commit_oid,
            "path": self.pin.path,
            "kind": self.pin.kind,
            "object_oid": self.pin.object_oid,
            "publication_status": self.publication_status,
        }


@dataclass(frozen=True)
class MaterializationResult:
    pin: Pin
    destination: Path
    publication_status: str

    def to_dict(self) -> dict:
        return {
            "status": "materialized",
            "operation": "materialize",
            "pin": self.pin.envelope,
            "repository": self.pin.repository,
            "commit_oid": self.pin.commit_oid,
            "path": self.pin.path,
            "kind": self.pin.kind,
            "destination": str(self.destination),
            "publication_status": self.publication_status,
        }


# --------------------------------------------------------------------------- #
# shared helpers
# --------------------------------------------------------------------------- #
def _require_local(repo: Repository) -> Path:
    local = repo.local
    if local is None:
        raise PinError("INVALID_REGISTRY", f"repository {repo.id!r} has no local_path")
    if not gitio.is_git_repo(local):
        raise PinError("INVALID_REGISTRY", f"repository {repo.id!r} local_path is not a git repo: {local}")
    actual = gitio.object_format(local)
    if actual != repo.object_format:
        raise PinError("REPOSITORY_MISMATCH",
                       f"repository {repo.id!r}: registry says {repo.object_format}, "
                       f"local clone is {actual}")
    return local


def _cache_repo(repo: Repository, cfg: Config) -> Path:
    path = cfg.cache_dir / f"{repo.id}.git"
    gitio.init_bare_cache(path, object_format=repo.object_format)
    return path


def _check_publication(repo: Repository, commit_hex: str, cfg: Config) -> Path:
    """Return the cache repo (now containing the published history) on success;
    raise COMMIT_NOT_PUBLISHED / REMOTE_UNAVAILABLE otherwise."""
    cache = _cache_repo(repo, cfg)
    last_transport_error: PinError | None = None
    for url in repo.fetch_urls:
        try:
            advertised = gitio.ls_remote(url, timeout=cfg.remote_timeout_seconds)
            allowed = [ref for _oid, ref in advertised if repo.ref_allowed(ref)]
            if not allowed:
                continue
            gitio.fetch_refs(cache, url, allowed, timeout=cfg.remote_timeout_seconds)
        except PinError as exc:
            if exc.code == "REMOTE_UNAVAILABLE":
                last_transport_error = exc
                continue
            raise
        if not gitio.has_commit(cache, commit_hex):
            continue
        for ref in allowed:
            cache_ref = "refs/hivepin/" + ref.removeprefix("refs/")
            if gitio.is_ancestor(cache, commit_hex, cache_ref):
                return cache
    if last_transport_error is not None:
        raise last_transport_error
    raise PinError("COMMIT_NOT_PUBLISHED",
                   f"commit {commit_hex[:12]} is not reachable from an allowed ref of "
                   f"repository {repo.id!r}",
                   allowed_ref_patterns=list(repo.allowed_ref_patterns))


def _resolve_source(repo: Repository, commit_hex: str, offline: bool,
                    cfg: Config) -> tuple[Path, str]:
    """The repository to read the commit from (the local clone if it has it, else
    the publication cache), and the publication status."""
    local = repo.local if (repo.local and gitio.is_git_repo(repo.local)) else None
    if local is not None and gitio.object_format(local) != repo.object_format:
        raise PinError("REPOSITORY_MISMATCH",
                       f"repository {repo.id!r} local clone object format disagrees with registry")

    publication_status = "not_checked"
    source: Path | None = None
    if local is not None and gitio.has_commit(local, commit_hex):
        source = local

    if not offline:
        cache = _check_publication(repo, commit_hex, cfg)
        publication_status = "verified"
        if source is None:
            source = cache
    elif source is None:
        cache = cfg.cache_dir / f"{repo.id}.git"
        if gitio.is_git_repo(cache) and gitio.has_commit(cache, commit_hex):
            source = cache
        else:
            raise PinError("COMMIT_NOT_FOUND",
                           f"commit {commit_hex[:12]} is not in any local cache (offline)")
    return source, publication_status


def _classify_entry(entry: TreeEntry) -> str:
    if entry.mode in UNSUPPORTED_MODES:
        raise PinError("UNSUPPORTED_OBJECT",
                       f"{UNSUPPORTED_MODES[entry.mode]} at {entry.path!r} is not supported in v1")
    if entry.type == "tree":
        return "tree"
    if entry.type == "blob":
        if entry.mode not in _FILE_MODES:
            raise PinError("UNSUPPORTED_OBJECT", f"file mode {entry.mode} at {entry.path!r}")
        return "file"
    raise PinError("UNSUPPORTED_OBJECT", f"object kind {entry.type!r} at {entry.path!r}")


def _scan_unsupported(source: Path, commit_hex: str, path: str) -> None:
    for e in gitio.list_tree(source, commit_hex, path):
        if e.mode in UNSUPPORTED_MODES:
            raise PinError("UNSUPPORTED_OBJECT",
                           f"tree contains a {UNSUPPORTED_MODES[e.mode]}: {e.path}")


def _rel_to_pin(entry_path: str, pin_path: str) -> str:
    if pin_path == WHOLE_TREE:
        return entry_path
    prefix = pin_path + "/"
    if not entry_path.startswith(prefix):
        raise PinError("INTERNAL_ERROR", f"entry {entry_path!r} not under {pin_path!r}")
    return entry_path[len(prefix):]


def _blob_entries(source: Path, commit_hex: str, pin_path: str, cfg: Config) -> list[dict]:
    """One record per regular file under the pinned path: {path (rel to pin root),
    mode, size, digest, oid}. Enforces the tree caps."""
    entries = gitio.list_tree(source, commit_hex, pin_path)
    files = [e for e in entries if e.type == "blob"]
    for e in entries:
        if e.mode in UNSUPPORTED_MODES:
            raise PinError("UNSUPPORTED_OBJECT",
                           f"tree contains a {UNSUPPORTED_MODES[e.mode]}: {e.path}")
    if len(files) > cfg.max_tree_files:
        raise PinError("LIMIT_EXCEEDED", f"tree has {len(files)} files (max {cfg.max_tree_files})")
    out: list[dict] = []
    total = 0
    for e in files:
        if e.mode not in _FILE_MODES:
            raise PinError("UNSUPPORTED_OBJECT", f"file mode {e.mode} at {e.path!r}")
        data = gitio.read_blob(source, e.oid, max_bytes=cfg.max_file_bytes)
        total += len(data)
        if total > cfg.max_tree_bytes:
            raise PinError("LIMIT_EXCEEDED", f"tree exceeds max_tree_bytes ({cfg.max_tree_bytes})")
        out.append({
            "path": check_tree_relpath(_rel_to_pin(e.path, pin_path)),
            "mode": e.mode,
            "size": len(data),
            "digest": sha256_hex(data),
            "oid": e.oid,
        })
    return out


def _content_digest(source: Path, pin: Pin, cfg: Config) -> str:
    if pin.kind == "file":
        data = gitio.read_blob(source, f"{pin.commit_hex}:{pin.path}", max_bytes=cfg.max_file_bytes)
        return sha256_hex(data)
    return manifest_digest(_blob_entries(source, pin.commit_hex, pin.path, cfg))


# --------------------------------------------------------------------------- #
# mint  (SPEC 12.1)
# --------------------------------------------------------------------------- #
def mint(repository: str, path: str | None, registry: Registry, *,
         commit: str | None = None, offline: bool = False,
         config: Config | None = None, version: int = 2) -> MintResult | MintResultV2:
    """Mint a pin. ``version=2`` (the default) pins a commit, narrowed to ``path``
    when one is given (None or '.' mean the whole commit); ``version=1`` mints a
    v1 file or tree pin and needs a path."""
    if version == 2:
        return _mint_v2(repository, path, registry, commit=commit, offline=offline, config=config)
    if version != 1:
        raise PinError("UNSUPPORTED_VERSION", f"cannot mint pin version {version!r}")
    if path is None:
        raise PinError("INVALID_PATH", "a v1 pin needs a path ('.' for the whole tree)")
    cfg = config or Config.load()
    repo = registry.get(repository)
    local = _require_local(repo)
    fmt = repo.object_format

    from .canonical import check_path
    path = check_path(path)

    if commit is None:
        commit_hex = gitio.head_commit(local)
        if gitio.path_status(local, path):
            raise PinError("DIRTY_PATH",
                           f"{path!r} differs from HEAD or has untracked files; commit before minting")
    else:
        commit_hex = gitio.resolve_commit(local, commit)
    if not gitio.has_commit(local, commit_hex):
        raise PinError("COMMIT_NOT_FOUND", f"{commit_hex[:12]} is not a commit in {repo.id!r}")

    entry = gitio.entry_at(local, commit_hex, path)
    kind = _classify_entry(entry)
    if kind == "tree":
        _scan_unsupported(local, commit_hex, path)

    pin = Pin(
        repository=repository,
        commit_oid=f"{fmt}:{commit_hex}",
        path=path,
        kind=kind,
        object_oid=f"{fmt}:{entry.oid}",
        content_digest="sha256:" + "0" * 64,   # placeholder, replaced below
        mode=entry.mode if kind == "file" else None,
    )
    digest = _content_digest(local, pin, cfg)
    pin = Pin(
        repository=repository, commit_oid=pin.commit_oid, path=path, kind=kind,
        object_oid=pin.object_oid, content_digest=digest,
        mode=entry.mode if kind == "file" else None,
    )

    publication_status = "not_checked"
    if not offline:
        _check_publication(repo, commit_hex, cfg)
        publication_status = "verified"
    return MintResult(pin=pin, publication_status=publication_status)


# --------------------------------------------------------------------------- #
# verify  (SPEC 12.2)
# --------------------------------------------------------------------------- #
def verify(pin: Pin | PinV2, registry: Registry, *, offline: bool = False,
           config: Config | None = None) -> VerificationResult | VerificationResultV2:
    if isinstance(pin, PinV2):
        return _verify_v2(pin, registry, offline=offline, config=config)
    cfg = config or Config.load()
    repo = registry.get(pin.repository)

    if repo.object_format != pin.object_format:
        raise PinError("OBJECT_FORMAT_MISMATCH",
                       f"pin uses {pin.object_format}, registry says {repo.object_format}")

    source, publication_status = _resolve_source(repo, pin.commit_hex, offline, cfg)

    entry = gitio.entry_at(source, pin.commit_hex, pin.path)
    actual_kind = _classify_entry(entry)
    if actual_kind != pin.kind:
        raise PinError("KIND_MISMATCH", f"pin says {pin.kind}, object is {actual_kind}")
    if pin.kind == "file" and entry.mode != pin.mode:
        raise PinError("MODE_MISMATCH", f"pin mode {pin.mode}, object mode {entry.mode}")
    if entry.oid != pin.object_hex:
        raise PinError("OBJECT_MISMATCH", f"pin object_oid {pin.object_hex[:12]}, actual {entry.oid[:12]}")
    if pin.kind == "tree":
        _scan_unsupported(source, pin.commit_hex, pin.path)

    actual_digest = _content_digest(source, pin, cfg)
    if actual_digest != pin.content_digest:
        raise PinError("CONTENT_MISMATCH",
                       "independently computed content digest does not match the pin")

    return VerificationResult(pin=pin, publication_status=publication_status,
                              _source=source, _entry=entry)


# --------------------------------------------------------------------------- #
# materialize  (SPEC 12.3)
# --------------------------------------------------------------------------- #
def materialize(pin: Pin | PinV2, destination: str | os.PathLike, registry: Registry, *,
                offline: bool = False,
                config: Config | None = None) -> MaterializationResult | MaterializationResultV2:
    if isinstance(pin, PinV2):
        return _materialize_v2(pin, destination, registry, offline=offline, config=config)
    cfg = config or Config.load()
    result = verify(pin, registry, offline=offline, config=cfg)
    source = result._source

    dest = Path(destination).expanduser()
    if dest.is_symlink() or dest.exists():
        raise PinError("DESTINATION_EXISTS", f"destination already exists: {dest}")
    parent = dest.parent
    parent.mkdir(parents=True, exist_ok=True)
    if parent.is_symlink():
        raise PinError("PATH_COLLISION", f"destination parent is a symlink: {parent}")

    tmp = Path(tempfile.mkdtemp(prefix=".hivepin-mat-", dir=parent))
    try:
        if pin.kind == "file":
            _write_file(tmp / pin.path, source, f"{pin.commit_hex}:{pin.path}", pin.mode, cfg, tmp)
            check_digest = sha256_hex((tmp / pin.path).read_bytes())
        else:
            entries = _blob_entries(source, pin.commit_hex, pin.path, cfg)
            for e in entries:
                target = tmp / (pin.path + "/" + e["path"] if pin.path != WHOLE_TREE else e["path"])
                _write_file(target, source, e["oid"], e["mode"], cfg, tmp)
            check_digest = manifest_digest([
                {"path": e["path"], "mode": e["mode"],
                 "size": (tmp / _dest_rel(pin, e)).stat().st_size,
                 "digest": sha256_hex((tmp / _dest_rel(pin, e)).read_bytes())}
                for e in entries
            ])
        if check_digest != pin.content_digest:
            raise PinError("CONTENT_MISMATCH", "materialized bytes do not match the pin's content digest")

        _assert_no_symlinks(tmp)
        os.replace(tmp, dest)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise

    return MaterializationResult(pin=pin, destination=dest,
                                 publication_status=result.publication_status)


def _dest_rel(pin: Pin, entry: dict) -> str:
    return pin.path + "/" + entry["path"] if pin.path != WHOLE_TREE else entry["path"]


def _write_file(target: Path, source: Path, spec: str, mode: str, cfg: Config, root: Path) -> None:
    # traversal guard BEFORE any filesystem access
    if ".." in target.parts or not str(target).startswith(str(root) + os.sep):
        raise PinError("PATH_COLLISION", f"refusing to write outside destination: {target}")
    data = gitio.read_blob(source, spec, max_bytes=cfg.max_file_bytes)
    # create parents, refusing to follow a symlink at any component
    _safe_mkdir_parents(target, root)
    if target.exists() or target.is_symlink():
        raise PinError("PATH_COLLISION", f"materialized path collides: {target}")
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o644)
    with open(fd, "wb") as fh:
        fh.write(data)
    if mode == "100755":
        target.chmod(0o755)


def _safe_mkdir_parents(target: Path, root: Path) -> None:
    rel = target.relative_to(root)
    cur = root
    for part in rel.parts[:-1]:
        cur = cur / part
        if cur.is_symlink():
            raise PinError("PATH_COLLISION", f"symlink in materialized path: {cur}")
        cur.mkdir(exist_ok=True)


def _assert_no_symlinks(root: Path) -> None:
    for base, dirs, files in os.walk(root):
        for name in dirs + files:
            p = Path(base) / name
            if p.is_symlink():
                raise PinError("UNSUPPORTED_OBJECT", f"symlink present after extraction: {p}")
            st = p.lstat()
            if not (stat.S_ISDIR(st.st_mode) or stat.S_ISREG(st.st_mode)):
                raise PinError("UNSUPPORTED_OBJECT", f"non-regular file after extraction: {p}")


from .v2 import (  # noqa: E402  (v2 builds on the helpers above)
    MaterializationResultV2,
    MintResultV2,
    VerificationResultV2,
    _materialize_v2,
    _mint_v2,
    _verify_v2,
)
