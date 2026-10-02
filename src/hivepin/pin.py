# SPDX-License-Identifier: GPL-3.0-or-later
"""The canonical pin object (SPEC.md sections 8, 9)."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass

from .canonical import (
    SCHEMA_VERSION,
    canonical_json_bytes,
    check_content_digest,
    check_oid,
    check_path,
    check_path_v2,
    check_repository_id,
)
from .errors import PinError

_REQUIRED = ("version", "repository", "commit_oid", "path", "kind", "object_oid", "content_digest")
_OPTIONAL = ("mode",)
_ALLOWED = frozenset(_REQUIRED + _OPTIONAL)
_FILE_MODES = ("100644", "100755")
_LABEL_PREFIX = "hivepin:v1:"


@dataclass(frozen=True)
class Pin:
    repository: str
    commit_oid: str          # algorithm-prefixed, e.g. "sha1:8b61..."
    path: str
    kind: str                # "file" | "tree"
    object_oid: str          # algorithm-prefixed
    content_digest: str      # "sha256:<64 hex>"
    mode: str | None = None  # required for kind == "file", absent for "tree"

    version = 1

    # ----------------------------------------------------------------- build
    def __post_init__(self) -> None:
        check_repository_id(self.repository)
        check_path(self.path)
        if self.kind not in ("file", "tree"):
            raise PinError("INVALID_PIN", f"kind must be 'file' or 'tree': {self.kind!r}")
        object_format = self.commit_oid.split(":", 1)[0]
        check_oid(self.commit_oid, object_format=object_format, field="commit_oid")
        check_oid(self.object_oid, object_format=object_format, field="object_oid")
        check_content_digest(self.content_digest)
        if self.kind == "file":
            if self.mode not in _FILE_MODES:
                raise PinError("INVALID_PIN", f"file pin needs mode 100644 or 100755: {self.mode!r}")
        elif self.mode is not None:
            raise PinError("INVALID_PIN", "tree pin must not carry a mode")

    @property
    def object_format(self) -> str:
        return self.commit_oid.split(":", 1)[0]

    @property
    def commit_hex(self) -> str:
        return self.commit_oid.split(":", 1)[1]

    @property
    def object_hex(self) -> str:
        return self.object_oid.split(":", 1)[1]

    # ------------------------------------------------------------ canonical
    def to_canonical_dict(self) -> dict:
        d = {
            "version": SCHEMA_VERSION,
            "repository": self.repository,
            "commit_oid": self.commit_oid,
            "path": self.path,
            "kind": self.kind,
            "object_oid": self.object_oid,
            "content_digest": self.content_digest,
        }
        if self.kind == "file":
            d["mode"] = self.mode
        return d

    def to_canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_canonical_dict())

    def to_file_bytes(self) -> bytes:
        """Canonical object plus the one trailing LF used when stored standalone."""
        return self.to_canonical_bytes() + b"\n"

    # -------------------------------------------------------------- parsing
    @classmethod
    def from_dict(cls, data: object) -> Pin:
        if not isinstance(data, dict):
            raise PinError("INVALID_PIN", "pin must be a JSON object")
        if data.get("version") != SCHEMA_VERSION:
            raise PinError("UNSUPPORTED_VERSION",
                           f"pin version must be {SCHEMA_VERSION}, got {data.get('version')!r}")
        unknown = set(data) - _ALLOWED
        if unknown:
            raise PinError("INVALID_PIN", f"pin has unknown field(s): {sorted(unknown)}")
        missing = [k for k in _REQUIRED if k not in data]
        if missing:
            raise PinError("INVALID_PIN", f"pin is missing field(s): {missing}")
        return cls(
            repository=data["repository"],
            commit_oid=data["commit_oid"],
            path=data["path"],
            kind=data["kind"],
            object_oid=data["object_oid"],
            content_digest=data["content_digest"],
            mode=data.get("mode"),
        )

    @classmethod
    def from_canonical_bytes(cls, raw: bytes) -> Pin:
        text = raw.decode("utf-8").rstrip("\n")
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise PinError("INVALID_PIN", f"pin is not valid JSON: {exc}") from exc
        pin = cls.from_dict(data)
        # round-trip: a canonical pin must equal its own re-serialization
        if pin.to_canonical_bytes() != text.encode("utf-8"):
            raise PinError("INVALID_PIN", "pin bytes are not in canonical form")
        return pin

    @classmethod
    def parse(cls, text: str) -> Pin:
        """Accept either a raw canonical JSON pin or a ``hivepin:v1:`` envelope."""
        text = text.strip()
        if text.startswith(_LABEL_PREFIX):
            payload = text[len(_LABEL_PREFIX):]
            pad = "=" * (-len(payload) % 4)
            try:
                raw = base64.urlsafe_b64decode(payload + pad)
            except (ValueError, base64.binascii.Error) as exc:
                raise PinError("INVALID_PIN", f"bad hivepin:v1 envelope: {exc}") from exc
            return cls.from_canonical_bytes(raw)
        return cls.from_canonical_bytes(text.encode("utf-8"))

    # --------------------------------------------------------------- labels
    @property
    def display_label(self) -> str:
        """Human shorthand. NON-AUTHORITATIVE - never store or accept downstream."""
        return f"{self.repository}:{self.path}@{self.commit_hex[:8]}"

    @property
    def envelope(self) -> str:
        """Reversible textual encoding for UIs (SPEC 9)."""
        b64 = base64.urlsafe_b64encode(self.to_canonical_bytes()).rstrip(b"=").decode("ascii")
        return _LABEL_PREFIX + b64


# --------------------------------------------------------------------------- #
# v2: the pin is a commit, optionally narrowed to a path (docs/pin-v2.md)
# --------------------------------------------------------------------------- #
_V2_ALLOWED = frozenset(("version", "repository", "commit_oid", "path"))
_V2_PREFIX = "hivepin:v2:"


@dataclass(frozen=True)
class PinV2:
    repository: str
    commit_oid: str          # algorithm-prefixed
    path: str | None = None  # None: the whole commit; else binding for consumers

    version = 2

    def __post_init__(self) -> None:
        check_repository_id(self.repository)
        check_oid(self.commit_oid, object_format=self.commit_oid.split(":", 1)[0], field="commit_oid")
        check_path_v2(self.path)

    @property
    def object_format(self) -> str:
        return self.commit_oid.split(":", 1)[0]

    @property
    def commit_hex(self) -> str:
        return self.commit_oid.split(":", 1)[1]

    def to_canonical_dict(self) -> dict:
        d = {"version": 2, "repository": self.repository, "commit_oid": self.commit_oid}
        if self.path is not None:
            d["path"] = self.path
        return d

    def to_canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_canonical_dict())

    def to_file_bytes(self) -> bytes:
        return self.to_canonical_bytes() + b"\n"

    @classmethod
    def from_dict(cls, data: object) -> PinV2:
        if not isinstance(data, dict):
            raise PinError("INVALID_PIN", "pin must be a JSON object")
        if data.get("version") != 2:
            raise PinError("UNSUPPORTED_VERSION", f"pin version must be 2, got {data.get('version')!r}")
        unknown = set(data) - _V2_ALLOWED
        if unknown:
            raise PinError("INVALID_PIN", f"pin has unknown field(s): {sorted(unknown)}")
        missing = [k for k in ("repository", "commit_oid") if k not in data]
        if missing:
            raise PinError("INVALID_PIN", f"pin is missing field(s): {missing}")
        if "path" in data and not isinstance(data["path"], str):
            raise PinError("INVALID_PIN", "path must be a string when present")
        return cls(repository=data["repository"], commit_oid=data["commit_oid"], path=data.get("path"))

    @property
    def display_label(self) -> str:
        """Human shorthand. NON-AUTHORITATIVE - never store or accept downstream."""
        if self.path is None:
            return f"{self.repository}@{self.commit_hex[:8]}"
        return f"{self.repository}:{self.path}@{self.commit_hex[:8]}"

    @property
    def envelope(self) -> str:
        b64 = base64.urlsafe_b64encode(self.to_canonical_bytes()).rstrip(b"=").decode("ascii")
        return _V2_PREFIX + b64


AnyPin = Pin | PinV2


def pin_from_dict(data: object) -> AnyPin:
    """A v1 or v2 pin from its JSON object, by its ``version``."""
    if isinstance(data, dict) and data.get("version") == 2:
        return PinV2.from_dict(data)
    if isinstance(data, dict) and data.get("version") not in (1, 2):
        raise PinError("UNSUPPORTED_VERSION", f"unsupported pin version {data.get('version')!r}")
    return Pin.from_dict(data)


def _from_canonical_bytes(raw: bytes) -> AnyPin:
    text = raw.decode("utf-8").rstrip("\n")
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise PinError("INVALID_PIN", f"pin is not valid JSON: {exc}") from exc
    pin = pin_from_dict(data)
    if pin.to_canonical_bytes() != text.encode("utf-8"):
        raise PinError("INVALID_PIN", "pin bytes are not in canonical form")
    return pin


def parse_pin(text: str) -> AnyPin:
    """Canonical JSON (v1 or v2), or a ``hivepin:v1:`` / ``hivepin:v2:`` envelope."""
    text = text.strip()
    for prefix, version in ((_LABEL_PREFIX, 1), (_V2_PREFIX, 2)):
        if text.startswith(prefix):
            payload = text[len(prefix):]
            try:
                raw = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
            except (ValueError, base64.binascii.Error) as exc:
                raise PinError("INVALID_PIN", f"bad {prefix} envelope: {exc}") from exc
            pin = _from_canonical_bytes(raw)
            if pin.version != version:
                raise PinError("INVALID_PIN", f"{prefix} envelope holds a version {pin.version} pin")
            return pin
    return _from_canonical_bytes(text.encode("utf-8"))
