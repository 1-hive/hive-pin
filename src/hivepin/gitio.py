# SPDX-License-Identifier: GPL-3.0-or-later
"""Thin, safe wrappers over git plumbing (SPEC.md section 16).

Rules enforced here:
  * child processes are argument arrays - never a shell string;
  * hooks are disabled on every invocation (core.hooksPath=/dev/null);
  * terminal credential prompts are disabled;
  * every call has an explicit timeout;
  * the caller's index, worktree, branches, tags, stash and remotes are never
    touched - object reads only, and network fetches land in a separate cache
    repository.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .errors import PinError

_BASE_ARGS = ["-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false"]
_ENV = {"GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"}

DEFAULT_TIMEOUT = 30.0


@dataclass(frozen=True)
class TreeEntry:
    mode: str
    type: str          # "blob" | "tree" | "commit"
    oid: str           # bare hex
    size: int | None   # blob byte size when known
    path: str          # path as recorded by git, relative to repo root


def _run(repo: Path | None, args: list[str], *, timeout: float,
         binary: bool = False, allow_fail: bool = False) -> subprocess.CompletedProcess:
    cmd = ["git"]
    if repo is not None:
        cmd += ["-C", str(repo)]
    cmd += _BASE_ARGS + args
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=not binary,
            timeout=timeout,
            check=False,
            env={**os.environ, **_ENV},
        )
    except subprocess.TimeoutExpired:
        raise PinError("REMOTE_UNAVAILABLE" if _is_network(args) else "INTERNAL_ERROR",
                       f"git {args[0]} timed out after {timeout}s") from None
    except FileNotFoundError:
        raise PinError("INTERNAL_ERROR", "git executable not found on PATH") from None
    if proc.returncode != 0 and not allow_fail:
        stderr = proc.stderr or (b"" if binary else "")
        detail = stderr.decode("utf-8", "replace") if isinstance(stderr, bytes) else stderr
        raise PinError("INTERNAL_ERROR", f"git {args[0]} failed: {detail.strip()}")
    return proc


def _is_network(args: list[str]) -> bool:
    return bool(args) and args[0] in ("fetch", "ls-remote", "clone")


# --------------------------------------------------------------------------- #
# local repository inspection
# --------------------------------------------------------------------------- #
def is_git_repo(path: Path) -> bool:
    if not path.is_dir():
        return False
    p = _run(path, ["rev-parse", "--git-dir"], timeout=DEFAULT_TIMEOUT, allow_fail=True)
    return p.returncode == 0


def object_format(repo: Path) -> str:
    fmt = _run(repo, ["rev-parse", "--show-object-format"], timeout=DEFAULT_TIMEOUT).stdout.strip()
    if fmt not in ("sha1", "sha256"):
        raise PinError("INTERNAL_ERROR", f"unexpected object format: {fmt!r}")
    return fmt


def object_type(repo: Path, oid: str) -> str | None:
    p = _run(repo, ["cat-file", "-t", oid], timeout=DEFAULT_TIMEOUT, allow_fail=True)
    return p.stdout.strip() if p.returncode == 0 else None


def has_commit(repo: Path, commit: str) -> bool:
    return object_type(repo, f"{commit}^{{commit}}") == "commit"


def resolve_commit(repo: Path, rev: str) -> str:
    """Resolve a rev (branch, tag, HEAD, abbreviated or full sha) to a full commit
    OID. Callers that need immutability must pass the result on, never the input."""
    p = _run(repo, ["rev-parse", "--verify", "--quiet", "--end-of-options", f"{rev}^{{commit}}"],
             timeout=DEFAULT_TIMEOUT, allow_fail=True)
    if p.returncode != 0 or not p.stdout.strip():
        raise PinError("COMMIT_NOT_FOUND", f"cannot resolve {rev!r} to a commit")
    return p.stdout.strip()


def head_commit(repo: Path) -> str:
    return resolve_commit(repo, "HEAD")


def path_status(repo: Path, path: str) -> str:
    """git status --porcelain for one pathspec; '' means clean & fully tracked."""
    spec = [] if path == "." else ["--", path]
    out = _run(repo, ["status", "--porcelain", "-z", "--untracked-files=all", *spec],
               timeout=DEFAULT_TIMEOUT).stdout
    return out.strip("\x00").strip()


def entry_at(repo: Path, commit: str, path: str) -> TreeEntry:
    """The single tree entry at <commit>:<path>. '.' resolves to the commit's root tree."""
    if path == ".":
        tree = _run(repo, ["rev-parse", "--verify", f"{commit}^{{tree}}"],
                    timeout=DEFAULT_TIMEOUT).stdout.strip()
        return TreeEntry(mode="040000", type="tree", oid=tree, size=None, path=".")
    p = _run(repo, ["ls-tree", "--full-tree", "--long", "-z", commit, "--", path],
             timeout=DEFAULT_TIMEOUT)
    rec = p.stdout.split("\x00", 1)[0] if p.stdout else ""
    if not rec:
        raise PinError("PATH_NOT_FOUND", f"path {path!r} does not exist at {commit[:12]}")
    return _parse_ls_tree_record(rec)


def list_tree(repo: Path, commit: str, path: str) -> list[TreeEntry]:
    """Recursive listing of every entry under <commit>:<path> (blobs and, with -t,
    the sub-tree markers are omitted; we only need blobs plus a scan for bad modes)."""
    args = ["ls-tree", "-r", "--long", "-z", "--full-tree", commit]
    if path != ".":
        args += ["--", path]
    p = _run(repo, args, timeout=DEFAULT_TIMEOUT)
    return [_parse_ls_tree_record(r) for r in p.stdout.split("\x00") if r]


def _parse_ls_tree_record(rec: str) -> TreeEntry:
    meta, _, name = rec.partition("\t")
    fields = meta.split()
    if len(fields) < 3:
        raise PinError("INTERNAL_ERROR", f"unparseable ls-tree record: {rec!r}")
    mode, typ, oid = fields[0], fields[1], fields[2]
    size: int | None = None
    if len(fields) >= 4 and fields[3].isdigit():
        size = int(fields[3])
    return TreeEntry(mode=mode, type=typ, oid=oid, size=size, path=name)


def read_blob(repo: Path, spec: str, *, max_bytes: int) -> bytes:
    """Raw bytes of a blob named by <oid> or <commit>:<path>. Refuses over max_bytes."""
    size_p = _run(repo, ["cat-file", "-s", spec], timeout=DEFAULT_TIMEOUT, allow_fail=True)
    stated = size_p.stdout.strip()
    if size_p.returncode == 0 and stated.isdigit() and int(stated) > max_bytes:
        raise PinError("LIMIT_EXCEEDED", f"blob {spec} exceeds max_file_bytes ({max_bytes})")
    data = _run(repo, ["cat-file", "blob", spec], timeout=DEFAULT_TIMEOUT, binary=True).stdout
    if len(data) > max_bytes:
        raise PinError("LIMIT_EXCEEDED", f"blob {spec} exceeds max_file_bytes ({max_bytes})")
    return data


def is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    p = _run(repo, ["merge-base", "--is-ancestor", ancestor, descendant],
             timeout=DEFAULT_TIMEOUT, allow_fail=True)
    if p.returncode == 0:
        return True
    if p.returncode == 1:
        return False
    raise PinError("INTERNAL_ERROR", f"merge-base failed: {p.stderr.strip()}")


# --------------------------------------------------------------------------- #
# publication cache  (SPEC 13)
# --------------------------------------------------------------------------- #
def init_bare_cache(path: Path, *, object_format: str) -> None:
    if is_git_repo(path):
        return
    path.mkdir(parents=True, exist_ok=True)
    _run(None, ["init", "--bare", f"--object-format={object_format}", "--quiet", str(path)],
         timeout=DEFAULT_TIMEOUT)


def ls_remote(url: str, *, timeout: float) -> list[tuple[str, str]]:
    """(oid, ref) advertised by url. REMOTE_UNAVAILABLE on any transport failure."""
    p = _run(None, ["ls-remote", "--", url], timeout=timeout, allow_fail=True)
    if p.returncode != 0:
        raise PinError("REMOTE_UNAVAILABLE", f"cannot reach {url}: {p.stderr.strip()}")
    out: list[tuple[str, str]] = []
    for line in p.stdout.splitlines():
        oid, _, ref = line.partition("\t")
        if ref and not ref.endswith("^{}"):
            out.append((oid.strip(), ref.strip()))
    return out


def fetch_refs(cache: Path, url: str, refs: list[str], *, timeout: float) -> None:
    """Fetch the given fully-qualified refs from url into refs/hivepin/* of the
    cache repo. Never writes FETCH_HEAD, tags, or the caller's refs."""
    if not refs:
        return
    refspecs = [f"+{r}:refs/hivepin/{r.removeprefix('refs/')}" for r in refs]
    p = _run(cache, ["fetch", "--quiet", "--no-tags", "--no-write-fetch-head",
                     "--prune", "--", url, *refspecs],
             timeout=timeout, allow_fail=True)
    if p.returncode != 0:
        raise PinError("REMOTE_UNAVAILABLE", f"fetch from {url} failed: {p.stderr.strip()}")
