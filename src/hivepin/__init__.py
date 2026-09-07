# SPDX-License-Identifier: GPL-3.0-or-later
"""hivepin - One Hive R0: immutable git pinning.

Public API:

    from hivepin import Pin, Registry, Config, mint, verify, materialize, PinError

See SPEC.md for the frozen formats and CONTRACT guarantees.
"""

from __future__ import annotations

from .config import Config
from .core import (
    MaterializationResult,
    MintResult,
    VerificationResult,
    materialize,
    mint,
    verify,
)
from .errors import ERROR_CODES, PinError
from .pin import Pin
from .registry import Registry, Repository

__version__ = "1.0.0"
SCHEMA_VERSION = 1
REGISTRY_VERSION = 1

__all__ = [
    "ERROR_CODES",
    "REGISTRY_VERSION",
    "SCHEMA_VERSION",
    "Config",
    "MaterializationResult",
    "MintResult",
    "Pin",
    "PinError",
    "Registry",
    "Repository",
    "VerificationResult",
    "__version__",
    "materialize",
    "mint",
    "verify",
]
