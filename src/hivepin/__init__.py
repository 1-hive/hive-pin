# SPDX-License-Identifier: GPL-3.0-or-later
"""hivepin - One Hive R0: immutable git pinning.

Public API:

    from hivepin import Pin, PinV2, parse_pin, pin_from_dict, Registry, Config,
                        mint, verify, materialize, PinError

``mint`` emits v2 pins (a commit, optionally narrowed to a path) unless asked for
``version=1``; ``verify`` and ``materialize`` accept both. See SPEC.md (v1) and
SPEC-v2.md for the formats.
"""

from __future__ import annotations

from .config import Config
from .core import (
    MaterializationResult,
    MaterializationResultV2,
    MintResult,
    MintResultV2,
    VerificationResult,
    VerificationResultV2,
    materialize,
    mint,
    verify,
)
from .errors import ERROR_CODES, PinError
from .pin import AnyPin, Pin, PinV2, parse_pin, pin_from_dict
from .registry import Registry, Repository

__version__ = "2.0.0"
SCHEMA_VERSION = 1          # the v1 pin schema; v2 pins are PinV2
PIN_VERSIONS = (1, 2)
REGISTRY_VERSION = 1

__all__ = [
    "ERROR_CODES",
    "PIN_VERSIONS",
    "REGISTRY_VERSION",
    "SCHEMA_VERSION",
    "AnyPin",
    "Config",
    "MaterializationResult",
    "MaterializationResultV2",
    "MintResult",
    "MintResultV2",
    "Pin",
    "PinError",
    "PinV2",
    "Registry",
    "Repository",
    "VerificationResult",
    "VerificationResultV2",
    "__version__",
    "materialize",
    "mint",
    "parse_pin",
    "pin_from_dict",
    "verify",
]
