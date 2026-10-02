# SPDX-License-Identifier: GPL-3.0-or-later
"""Pin format v2: mint, verify and materialize a commit (docs/pin-v2.md).

Integrity is the git hash chain, re-computed here: every commit, tree and blob
read is re-hashed and compared with the OID it is stored under, so nothing is
trusted from a cache or a local clone. Symlinks are materialized only when they
resolve inside the materialized root; submodules are never fetched. Entries not
created are reported in ``omitted``, never dropped silently.
"""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import gitio
from .canonical import (
    WHOLE_TREE,
    check_path,
    commit_tree,
    git_object_oid,
    parse_tree,
    tree_entry_name,
)
from .config import Config
from .core import _check_publication, _require_local, _resolve_source, _safe_mkdir_parents
from .errors import PinError
from .pin import PinV2
from .registry import Registry

_FILE_MODES = ("100644", "100755")
_TREE = "040000"
_SYMLINK = "120000"
_GITLINK = "160000"
_MAX_HOPS = 40


# --------------------------------------------------------------------------- #
# results
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class MintResultV2:
    pin: PinV2
    publication_status: str
    kind: str                         # "commit" | "tree" | "file"

    def to_dict(self) -> dict:
        return {
            "status": "minted", "operation": "mint", "pin": self.pin.envelope,
            "canonical": self.pin.to_canonical_dict(), "label": self.pin.display_label,
            "repository": self.pin.repository, "commit_oid": self.pin.commit_oid,
            "path": self.pin.path, "kind": self.kind,
            "publication_status": self.publication_status,
        }


@dataclass(frozen=True)
class VerificationResultV2:
    pin: PinV2
    publication_status: str
    kind: str
    _source: Path = field(repr=False)

    def to_dict(self) -> dict:
        return {
            "status": "verified", "operation": "verify", "pin": self.pin.envelope,
            "repository": self.pin.repository, "commit_oid": self.pin.commit_oid,
            "path": self.pin.path, "kind": self.kind,
            "publication_status": self.publication_status,
        }


@dataclass(frozen=True)
class MaterializationResultV2:
    pin: PinV2
    destination: Path
    publication_status: str
    omitted: tuple[dict, ...] = ()

    def to_dict(self) -> dict:
        return {
            "status": "materialized", "operation": "materialize", "pin": self.pin.envelope,
            "repository": self.pin.repository, "commit_oid": self.pin.commit_oid,
            "path": self.pin.path, "destination": str(self.destination),
            "publication_status": self.publication_status, "omitted": list(self.omitted),
        }


# --------------------------------------------------------------------------- #
# reading with re-hashing
# --------------------------------------------------------------------------- #
class _Reader:
    def __init__(self, source: Path, object_format: str, cfg: Config) -> None:
        self.fmt = object_format
        self.cfg = cfg
        self.objects = gitio.ObjectReader(source)

    def read(self, oid: str, want: str) -> bytes:
        typ, data = self.objects.read(oid, max_bytes=self.cfg.max_file_bytes)
        if typ != want:
            raise PinError("OBJECT_MISMATCH", f"object {oid[:12]} is a {typ}, expected a {want}")
        if git_object_oid(typ, data, self.fmt) != oid:
            raise PinError("OBJECT_MISMATCH", f"object {oid[:12]} does not hash to its OID")
        return data

    def close(self) -> None:
        self.objects.close()


def _kind(mode: str, path: str) -> str:
    if mode == _TREE:
        return "tree"
    if mode in _FILE_MODES:
        return "file"
    what = {_SYMLINK: "a symlink", _GITLINK: "a submodule"}.get(mode, f"mode {mode}")
    raise PinError("UNSUPPORTED_OBJECT", f"the pinned path {path!r} is {what}")


def _walk_to(reader: _Reader, commit_hex: str, path: str | None) -> tuple[str, str]:
    """Re-hash the commit and every tree down to ``path`` (and the object at it).
    Returns (mode, oid) of the pinned object; the whole commit is its root tree."""
    mode, oid = _TREE, commit_tree(reader.read(commit_hex, "commit"))
    for comp in (path.split("/") if path else []):
        if mode != _TREE:
            raise PinError("PATH_NOT_FOUND", f"path {path!r}: {comp!r} is under a non-directory")
        want = comp.encode("utf-8")
        hit = [(m, o) for m, name, o in parse_tree(reader.read(oid, "tree"), reader.fmt) if name == want]
        if not hit:
            raise PinError("PATH_NOT_FOUND", f"path {path!r} does not exist at {commit_hex[:12]}")
        mode, oid = hit[0]
    kind = _kind(mode, path or WHOLE_TREE)
    reader.read(oid, "tree" if kind == "tree" else "blob")
    return mode, oid


# --------------------------------------------------------------------------- #
# mint / verify
# --------------------------------------------------------------------------- #
def _input_path(path: str | None) -> str | None:
    """Callers may say '.' for the whole commit; the pin then has no path."""
    if path is None or path == WHOLE_TREE:
        return None
    return check_path(path)


def _mint_v2(repository: str, path: str | None, registry: Registry, *, commit: str | None,
             offline: bool, config: Config | None) -> MintResultV2:
    cfg = config or Config.load()
    repo = registry.get(repository)
    local = _require_local(repo)
    path = _input_path(path)

    if commit is None:
        commit_hex = gitio.head_commit(local)
        if gitio.path_status(local, path or WHOLE_TREE):
            raise PinError("DIRTY_PATH", f"{path or 'the worktree'} differs from HEAD or has "
                                         "untracked files; commit before minting")
    else:
        commit_hex = gitio.resolve_commit(local, commit)
    if not gitio.has_commit(local, commit_hex):
        raise PinError("COMMIT_NOT_FOUND", f"{commit_hex[:12]} is not a commit in {repo.id!r}")

    reader = _Reader(local, repo.object_format, cfg)
    try:
        mode, _ = _walk_to(reader, commit_hex, path)
    finally:
        reader.close()

    pin = PinV2(repository=repository, commit_oid=f"{repo.object_format}:{commit_hex}", path=path)
    publication_status = "not_checked"
    if not offline:
        _check_publication(repo, commit_hex, cfg)
        publication_status = "verified"
    return MintResultV2(pin=pin, publication_status=publication_status,
                        kind="commit" if path is None else _kind(mode, path))


def _verify_v2(pin: PinV2, registry: Registry, *, offline: bool,
               config: Config | None) -> VerificationResultV2:
    cfg = config or Config.load()
    repo = registry.get(pin.repository)
    if repo.object_format != pin.object_format:
        raise PinError("OBJECT_FORMAT_MISMATCH",
                       f"pin uses {pin.object_format}, registry says {repo.object_format}")
    source, publication_status = _resolve_source(repo, pin.commit_hex, offline, cfg)
    reader = _Reader(source, repo.object_format, cfg)
    try:
        mode, _ = _walk_to(reader, pin.commit_hex, pin.path)
    finally:
        reader.close()
    return VerificationResultV2(pin=pin, publication_status=publication_status,
                                kind="commit" if pin.path is None else _kind(mode, pin.path),
                                _source=source)


# --------------------------------------------------------------------------- #
# materialize
# --------------------------------------------------------------------------- #
def _materialize_v2(pin: PinV2, destination: str | os.PathLike, registry: Registry, *,
                    offline: bool, config: Config | None) -> MaterializationResultV2:
    cfg = config or Config.load()
    checked = _verify_v2(pin, registry, offline=offline, config=cfg)

    dest = Path(destination).expanduser()
    if dest.is_symlink() or dest.exists():
        raise PinError("DESTINATION_EXISTS", f"destination already exists: {dest}")
    parent = dest.parent
    parent.mkdir(parents=True, exist_ok=True)
    if parent.is_symlink():
        raise PinError("PATH_COLLISION", f"destination parent is a symlink: {parent}")

    tmp = Path(tempfile.mkdtemp(prefix=".hivepin-mat-", dir=parent))
    reader = _Reader(checked._source, registry.get(pin.repository).object_format, cfg)
    try:
        mode, oid = _walk_to(reader, pin.commit_hex, pin.path)
        # the materialized root: the repository-relative location of the pin
        rel_root = pin.path.split("/") if pin.path else []
        omitted: list[dict] = []
        created: set[tuple[str, ...]] = set()
        if mode == _TREE:
            omitted, created = _extract_tree(reader, oid, tmp, rel_root, cfg)
        else:
            _write(tmp, rel_root, reader.read(oid, "blob"), mode)
        _check_output(tmp, created)
        os.replace(tmp, dest)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    finally:
        reader.close()
    return MaterializationResultV2(pin=pin, destination=dest,
                                   publication_status=checked.publication_status,
                                   omitted=tuple(omitted))


def _extract_tree(reader: _Reader, root_oid: str, tmp: Path, rel_root: list[str],
                  cfg: Config) -> tuple[list[dict], set[tuple[str, ...]]]:
    """Write every directory and regular file, then the symlinks that stay inside.
    Returns the omitted entries and the created links (as paths under ``tmp``).
    ``path`` tuples below are relative to the pinned root."""
    links: dict[tuple[str, ...], bytes] = {}
    omitted: list[dict] = []
    files = 0
    total = 0
    seen: set[tuple[str, ...]] = set()
    _ensure_dir(tmp, rel_root)

    stack: list[tuple[tuple[str, ...], str]] = [((), root_oid)]
    while stack:
        here, oid = stack.pop()
        for mode, raw, child in parse_tree(reader.read(oid, "tree"), reader.fmt):
            path = (*here, tree_entry_name(raw))
            if path in seen:
                raise PinError("PATH_COLLISION", f"tree entries collide after normalization: "
                                                 f"{'/'.join(path)}")
            seen.add(path)
            if mode == _TREE:
                _ensure_dir(tmp, rel_root + list(path))
                stack.append((path, child))
            elif mode in _FILE_MODES:
                files += 1
                if files > cfg.max_tree_files:
                    raise PinError("LIMIT_EXCEEDED", f"tree has over {cfg.max_tree_files} files")
                data = reader.read(child, "blob")
                total += len(data)
                if total > cfg.max_tree_bytes:
                    raise PinError("LIMIT_EXCEEDED",
                                   f"tree exceeds max_tree_bytes ({cfg.max_tree_bytes})")
                _write(tmp, rel_root + list(path), data, mode)
            elif mode == _SYMLINK:
                links[path] = reader.read(child, "blob")
            elif mode == _GITLINK:
                omitted.append({"path": "/".join(rel_root + list(path)), "kind": "submodule",
                                "reason": "submodule", "commit_oid": f"{reader.fmt}:{child}"})
            else:
                raise PinError("UNSUPPORTED_OBJECT", f"mode {mode} at {'/'.join(path)!r}")

    # symlinks last, so no write above could go through one
    created: set[tuple[str, ...]] = set()
    for path, target in sorted(links.items()):
        if _link_stays_inside(path, target, links):
            os.symlink(target, tmp.joinpath(*rel_root, *path))
            created.add(tuple(rel_root) + path)
        else:
            omitted.append({"path": "/".join(rel_root + list(path)), "kind": "symlink",
                            "reason": "symlink_target"})
    return omitted, created


def _link_stays_inside(path: tuple[str, ...], target: bytes,
                       links: dict[tuple[str, ...], bytes]) -> bool:
    """Resolve ``target`` the way the OS will, component by component inside the
    pinned tree, following in-tree links. A lexical check is not enough: with
    ``d/l -> ..``, ``d/l/../x`` looks inside but resolves outside."""
    hops = [0]

    def resolve(base: list[str], tgt: bytes) -> list[str] | None:
        if not tgt or b"\x00" in tgt or tgt.startswith(b"/"):
            return None
        try:
            text = tgt.decode("utf-8")
        except UnicodeDecodeError:
            return None
        cur = list(base)
        for comp in text.split("/"):
            if comp in ("", "."):
                continue
            if comp == "..":
                if not cur:
                    return None
                cur.pop()
                continue
            cur.append(comp)
            key = tuple(cur)
            if key in links:
                hops[0] += 1
                if hops[0] > _MAX_HOPS:
                    return None
                nxt = resolve(cur[:-1], links[key])
                if nxt is None:
                    return None
                cur = nxt
        return cur

    return resolve(list(path[:-1]), target) is not None


def _ensure_dir(tmp: Path, parts: list[str]) -> None:
    cur = tmp
    for part in parts:
        cur = cur / part
        if cur.is_symlink():
            raise PinError("PATH_COLLISION", f"symlink in materialized path: {cur}")
        cur.mkdir(exist_ok=True)


def _write(tmp: Path, parts: list[str], data: bytes, mode: str) -> None:
    target = tmp.joinpath(*parts)
    _safe_mkdir_parents(target, tmp)
    if target.exists() or target.is_symlink():
        raise PinError("PATH_COLLISION", f"materialized path collides: {target}")
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o644)
    with open(fd, "wb") as fh:
        fh.write(data)
    if mode == "100755":
        target.chmod(0o755)


def _check_output(tmp: Path, links: set[tuple[str, ...]]) -> None:
    """Only directories, regular files and the links we created may exist."""
    for base, dirnames, filenames in os.walk(tmp):
        for name in dirnames + filenames:
            p = Path(base) / name
            st = p.lstat()
            if stat.S_ISLNK(st.st_mode):
                if tuple(p.relative_to(tmp).parts) not in links:
                    raise PinError("UNSUPPORTED_OBJECT", f"unexpected symlink after extraction: {p}")
            elif not (stat.S_ISDIR(st.st_mode) or stat.S_ISREG(st.st_mode)):
                raise PinError("UNSUPPORTED_OBJECT", f"non-regular file after extraction: {p}")


# --------------------------------------------------------------------------- #
# ancestry
# --------------------------------------------------------------------------- #
def is_ancestor(ancestor: PinV2, descendant: PinV2, registry: Registry, *,
                offline: bool = False, config: Config | None = None) -> bool:
    """Whether ``ancestor``'s commit is an ancestor of (or equal to) ``descendant``'s.

    Both must be v2 pins of the same repository; both are verified first (with the
    publication check unless offline). Used to check that a reviewed change starts
    where it says it does (a ``base`` before its ``code``)."""
    if not (isinstance(ancestor, PinV2) and isinstance(descendant, PinV2)):
        raise PinError("INVALID_PIN", "ancestry is defined for v2 pins only")
    if ancestor.repository != descendant.repository:
        raise PinError("REPOSITORY_MISMATCH", "ancestry needs two pins of one repository")
    cfg = config or Config.load()
    a = _verify_v2(ancestor, registry, offline=offline, config=cfg)
    d = _verify_v2(descendant, registry, offline=offline, config=cfg)
    for source in dict.fromkeys((d._source, a._source)):
        if gitio.has_commit(source, ancestor.commit_hex) and gitio.has_commit(source, descendant.commit_hex):
            return gitio.is_ancestor(source, ancestor.commit_hex, descendant.commit_hex)
    raise PinError("COMMIT_NOT_FOUND", "no local source holds both commits")
