# SPDX-License-Identifier: GPL-3.0-or-later
"""Repository registry (SPEC.md section 7).

Every ``repository`` id in a pin resolves through a versioned, operator-controlled
registry. Repository identity is never inferred from the current directory.
"""

from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass, field
from pathlib import Path

from .canonical import check_repository_id
from .errors import PinError

REGISTRY_VERSION = 1
_DEFAULT_ALLOWED_REFS = ("refs/heads/*", "refs/tags/*")


@dataclass(frozen=True)
class Repository:
    id: str
    fetch_urls: tuple[str, ...]
    object_format: str = "sha1"
    allowed_ref_patterns: tuple[str, ...] = _DEFAULT_ALLOWED_REFS
    # Local working copy or mirror used to read objects. Optional: publication
    # checks and materialization can run purely from fetched cache, but mint needs it.
    local_path: str | None = None

    @property
    def local(self) -> Path | None:
        return Path(self.local_path).expanduser() if self.local_path else None

    def ref_allowed(self, ref: str) -> bool:
        return any(fnmatch.fnmatchcase(ref, pat) for pat in self.allowed_ref_patterns)


@dataclass(frozen=True)
class Registry:
    repositories: dict[str, Repository] = field(default_factory=dict)
    source: Path | None = None

    def get(self, repo_id: str) -> Repository:
        try:
            return self.repositories[repo_id]
        except KeyError:
            raise PinError(
                "UNKNOWN_REPOSITORY",
                f"repository {repo_id!r} is not in the registry",
                known=sorted(self.repositories),
                registry=str(self.source) if self.source else None,
            ) from None

    @classmethod
    def load(cls, path: Path) -> Registry:
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise PinError("INVALID_REGISTRY", f"registry file not found: {path}") from None
        except OSError as exc:
            raise PinError("INVALID_REGISTRY", f"cannot read registry {path}: {exc}") from exc
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise PinError("INVALID_REGISTRY", f"registry is not valid JSON: {exc}") from exc
        return cls.from_dict(data, source=path)

    @classmethod
    def from_dict(cls, data: object, *, source: Path | None = None) -> Registry:
        if not isinstance(data, dict):
            raise PinError("INVALID_REGISTRY", "registry must be a JSON object")
        if data.get("version") != REGISTRY_VERSION:
            raise PinError("UNSUPPORTED_VERSION",
                           f"registry version must be {REGISTRY_VERSION}, got {data.get('version')!r}")
        repos_in = data.get("repositories")
        if not isinstance(repos_in, dict) or not repos_in:
            raise PinError("INVALID_REGISTRY", "registry has no repositories")
        repos: dict[str, Repository] = {}
        for rid, spec in repos_in.items():
            repos[rid] = _repository_from_spec(rid, spec)
        return cls(repositories=repos, source=source)


def _repository_from_spec(rid: str, spec: object) -> Repository:
    check_repository_id(rid)
    if not isinstance(spec, dict):
        raise PinError("INVALID_REGISTRY", f"repository {rid!r} must be an object")

    urls = spec.get("fetch_urls")
    if not isinstance(urls, list) or not urls or not all(isinstance(u, str) and u for u in urls):
        raise PinError("INVALID_REGISTRY", f"repository {rid!r} needs a non-empty fetch_urls list")
    if any(_looks_like_credential(u) for u in urls):
        raise PinError("INVALID_REGISTRY", f"repository {rid!r}: credentials must not be stored in URLs")

    obj_fmt = spec.get("object_format", "sha1")
    if obj_fmt not in ("sha1", "sha256"):
        raise PinError("INVALID_REGISTRY", f"repository {rid!r}: object_format must be sha1 or sha256")

    patterns = spec.get("allowed_ref_patterns", list(_DEFAULT_ALLOWED_REFS))
    if not isinstance(patterns, list) or not all(isinstance(p, str) and p for p in patterns):
        raise PinError("INVALID_REGISTRY", f"repository {rid!r}: allowed_ref_patterns must be strings")

    local = spec.get("local_path")
    if local is not None and not isinstance(local, str):
        raise PinError("INVALID_REGISTRY", f"repository {rid!r}: local_path must be a string")

    unknown = set(spec) - {"fetch_urls", "object_format", "allowed_ref_patterns", "local_path"}
    if unknown:
        raise PinError("INVALID_REGISTRY", f"repository {rid!r}: unknown keys {sorted(unknown)}")

    return Repository(
        id=rid,
        fetch_urls=tuple(urls),
        object_format=obj_fmt,
        allowed_ref_patterns=tuple(patterns),
        local_path=local,
    )


def _looks_like_credential(url: str) -> bool:
    # https://user:pass@host/... - reject the userinfo form outright
    _, sep, rest = url.partition("://")
    return bool(sep) and "@" in rest.split("/", 1)[0]
