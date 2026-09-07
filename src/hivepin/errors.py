# SPDX-License-Identifier: GPL-3.0-or-later
"""Stable error codes and process exit codes (SPEC.md sections 15, 14)."""

from __future__ import annotations

# --- process exit codes -----------------------------------------------------
EXIT_OK = 0
EXIT_USAGE = 2          # invalid invocation or malformed input
EXIT_VERIFY = 3         # verification failure
EXIT_REMOTE = 4         # remote temporarily unavailable
EXIT_INTERNAL = 5       # local I/O or unexpected internal failure

# --- error code -> exit code ---------------------------------------------------
_EXIT_FOR_CODE = {
    "INVALID_PIN": EXIT_USAGE,
    "UNSUPPORTED_VERSION": EXIT_USAGE,
    "INVALID_PATH": EXIT_USAGE,
    "INVALID_REGISTRY": EXIT_USAGE,
    "DESTINATION_EXISTS": EXIT_USAGE,
    "UNKNOWN_REPOSITORY": EXIT_VERIFY,
    "REPOSITORY_MISMATCH": EXIT_VERIFY,
    "OBJECT_FORMAT_MISMATCH": EXIT_VERIFY,
    "COMMIT_NOT_FOUND": EXIT_VERIFY,
    "COMMIT_NOT_PUBLISHED": EXIT_VERIFY,
    "PATH_NOT_FOUND": EXIT_VERIFY,
    "DIRTY_PATH": EXIT_VERIFY,
    "KIND_MISMATCH": EXIT_VERIFY,
    "UNSUPPORTED_OBJECT": EXIT_VERIFY,
    "MODE_MISMATCH": EXIT_VERIFY,
    "OBJECT_MISMATCH": EXIT_VERIFY,
    "CONTENT_MISMATCH": EXIT_VERIFY,
    "PATH_COLLISION": EXIT_VERIFY,
    "LIMIT_EXCEEDED": EXIT_VERIFY,
    "REMOTE_UNAVAILABLE": EXIT_REMOTE,
    "INTERNAL_ERROR": EXIT_INTERNAL,
}

ERROR_CODES = frozenset(_EXIT_FOR_CODE)


class PinError(Exception):
    """A typed failure. ``code`` is one of :data:`ERROR_CODES` and is the stable
    contract; ``message`` is human-readable and must never be parsed. ``context``
    holds safe structured fields (never credentials or secret-bearing output)."""

    def __init__(self, code: str, message: str, **context: object) -> None:
        if code not in _EXIT_FOR_CODE:
            raise AssertionError(f"unknown error code: {code!r}")
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.context = context

    @property
    def exit_code(self) -> int:
        return _EXIT_FOR_CODE[self.code]

    def to_dict(self) -> dict:
        return {
            "status": "error",
            "code": self.code,
            "message": self.message,
            **self.context,
        }
