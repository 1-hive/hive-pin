# SPDX-License-Identifier: GPL-3.0-or-later
"""Canonical encodings and validation primitives (SPEC.md sections 8, 10, 11).

Everything here is pure: no git, no filesystem, no network. Two conforming
implementations that agree on this module produce byte-identical pins.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata

from .errors import PinError

SCHEMA_VERSION = 1

_REPO_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_HEX_RE = {"sha1": re.compile(r"^[0-9a-f]{40}$"), "sha256": re.compile(r"^[0-9a-f]{64}$")}
_OID_RE = re.compile(r"^(sha1|sha256):([0-9a-f]+)$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_FILE_MODES = ("100644", "100755")

# git tree entry modes we refuse in v1
UNSUPPORTED_MODES = {"120000": "symlink", "160000": "submodule/gitlink"}

WHOLE_TREE = "."


# --------------------------------------------------------------------------- #
# repository ids
# --------------------------------------------------------------------------- #
def check_repository_id(value: str) -> str:
    if not isinstance(value, str) or not _REPO_ID_RE.match(value):
        raise PinError("INVALID_PIN", f"repository id must match {_REPO_ID_RE.pattern!r}: {value!r}")
    return value


# --------------------------------------------------------------------------- #
# object identifiers  (SPEC 8.2)
# --------------------------------------------------------------------------- #
def check_oid(value: str, *, object_format: str, field: str) -> str:
    m = _OID_RE.match(value or "")
    if not m:
        raise PinError("INVALID_PIN", f"{field} must be 'sha1:<hex>' or 'sha256:<hex>': {value!r}")
    algo, hexdigits = m.group(1), m.group(2)
    if algo != object_format:
        raise PinError(
            "OBJECT_FORMAT_MISMATCH",
            f"{field} uses {algo} but repository object format is {object_format}",
        )
    if not _HEX_RE[algo].match(hexdigits):
        raise PinError("INVALID_PIN", f"{field} has wrong hex length for {algo}: {value!r}")
    return value


def check_content_digest(value: str) -> str:
    if not isinstance(value, str) or not _DIGEST_RE.match(value):
        raise PinError("INVALID_PIN", f"content_digest must be 'sha256:<64 hex>': {value!r}")
    return value


def sha256_hex(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------- #
# paths  (SPEC 10)
# --------------------------------------------------------------------------- #
_FORBIDDEN_CHARS = ("\x00", "\r", "\n", "\\")


def check_path(value: str) -> str:
    """Validate a repository-relative POSIX path. Returns it unchanged (v1 does not
    silently normalize - a non-NFC path is an error, not something to fix)."""
    if not isinstance(value, str) or value == "":
        raise PinError("INVALID_PATH", "path must be a non-empty string")
    if value == WHOLE_TREE:
        return value
    if any(c in value for c in _FORBIDDEN_CHARS):
        raise PinError("INVALID_PATH", "path contains NUL, CR, LF, or backslash")
    if value != unicodedata.normalize("NFC", value):
        raise PinError("INVALID_PATH", f"path is not NFC-normalized: {value!r}")
    if value.startswith("/"):
        raise PinError("INVALID_PATH", "path must be relative to the repository root")
    parts = value.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise PinError("INVALID_PATH", f"path has an empty, '.', or '..' component: {value!r}")
    return value


# --------------------------------------------------------------------------- #
# canonical JSON  (SPEC 8.3)
# --------------------------------------------------------------------------- #
def canonical_json_bytes(obj: dict) -> bytes:
    """UTF-8, no BOM, no insignificant whitespace, keys ordered by Unicode code
    point. No trailing newline (that is a storage delimiter, added by callers that
    write a standalone file)."""
    return json.dumps(
        obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


# --------------------------------------------------------------------------- #
# tree manifest  (SPEC 11.2)
# --------------------------------------------------------------------------- #
def manifest_line(*, path: str, mode: str, size: int, digest: str) -> bytes:
    if mode not in _FILE_MODES:
        raise PinError("UNSUPPORTED_OBJECT", f"manifest entry has non-file mode {mode}: {path}")
    check_content_digest(digest)
    obj = {"digest": digest, "mode": mode, "path": path, "size": int(size)}
    return canonical_json_bytes(obj) + b"\n"


def check_tree_relpath(value: str) -> str:
    """Path of a file *inside* a pinned tree: NFC-normalized (not merely rejected),
    then structurally validated. Returns the normalized form."""
    if not isinstance(value, str) or value in ("", "."):
        raise PinError("INVALID_PATH", f"bad tree entry path: {value!r}")
    if any(c in value for c in _FORBIDDEN_CHARS):
        raise PinError("INVALID_PATH", "tree entry path contains NUL, CR, LF, or backslash")
    norm = unicodedata.normalize("NFC", value)
    if norm.startswith("/") or any(p in ("", ".", "..") for p in norm.split("/")):
        raise PinError("INVALID_PATH", f"unsafe tree entry path: {value!r}")
    return norm


def build_manifest(entries: list[dict]) -> bytes:
    """entries: list of {path, mode, size, digest}, one per regular file, paths
    relative to the pinned tree root. Sorted here by the UTF-8 byte sequence of the
    normalized relative path (SPEC 11.2 - deliberately bytewise, unlike the
    code-point ordering of JSON object keys). Two paths that collide after NFC
    normalization are a PATH_COLLISION."""
    normed = []
    seen: set[str] = set()
    for e in entries:
        p = check_tree_relpath(e["path"])
        if p in seen:
            raise PinError("PATH_COLLISION", f"tree entries collide after normalization: {p}")
        seen.add(p)
        normed.append({**e, "path": p})
    ordered = sorted(normed, key=lambda e: e["path"].encode("utf-8"))
    out = bytearray()
    for e in ordered:
        out += manifest_line(path=e["path"], mode=e["mode"], size=e["size"], digest=e["digest"])
    return bytes(out)


def manifest_digest(entries: list[dict]) -> str:
    return sha256_hex(build_manifest(entries))


# --------------------------------------------------------------------------- #
# git objects  (v2 integrity: re-hash instead of an independent digest)
# --------------------------------------------------------------------------- #
_OID_BYTES = {"sha1": 20, "sha256": 32}


def git_object_oid(typ: str, data: bytes, object_format: str) -> str:
    """Bare hex OID git assigns to an object of this type and content."""
    h = hashlib.new(object_format)
    h.update(f"{typ} {len(data)}\x00".encode("ascii"))
    h.update(data)
    return h.hexdigest()


def parse_tree(data: bytes, object_format: str) -> list[tuple[str, bytes, str]]:
    """Entries of a raw tree object: (mode, name bytes, bare hex oid)."""
    n = _OID_BYTES[object_format]
    out: list[tuple[str, bytes, str]] = []
    i = 0
    while i < len(data):
        sp = data.find(b" ", i)
        nul = data.find(b"\x00", sp + 1)
        if sp < 0 or nul < 0 or nul + 1 + n > len(data):
            raise PinError("OBJECT_MISMATCH", "malformed tree object")
        mode = data[i:sp].decode("ascii", "replace")
        out.append((mode.zfill(6), data[sp + 1:nul], data[nul + 1:nul + 1 + n].hex()))
        i = nul + 1 + n
    return out


def commit_tree(data: bytes) -> str:
    """The root tree OID named by a raw commit object."""
    first = data.split(b"\n", 1)[0]
    if not first.startswith(b"tree "):
        raise PinError("OBJECT_MISMATCH", "malformed commit object")
    return first[5:].decode("ascii")


def check_path_v2(value: str | None) -> str | None:
    """A v2 pin path: absent (None) for the whole commit, else a v1-style path.
    '.' is not a v2 path: the whole commit is written by leaving the path out."""
    if value is None:
        return None
    if value == WHOLE_TREE:
        raise PinError("INVALID_PATH", "a v2 pin of the whole commit has no path ('.' is v1 only)")
    return check_path(value)


def tree_entry_name(raw: bytes) -> str:
    """One component of a path inside a pinned tree, validated (v1 §10 rules)."""
    try:
        name = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise PinError("INVALID_PATH", f"tree entry name is not UTF-8: {raw!r}") from None
    if name in ("", ".", "..") or "/" in name or any(c in name for c in _FORBIDDEN_CHARS):
        raise PinError("INVALID_PATH", f"unsafe tree entry name: {name!r}")
    if name.lower() == ".git":
        raise PinError("INVALID_PATH", "a tree entry named .git is never materialized")
    return unicodedata.normalize("NFC", name)
