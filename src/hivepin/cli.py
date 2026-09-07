# SPDX-License-Identifier: GPL-3.0-or-later
"""``hive-pin`` command-line interface (SPEC.md sections 12, 14, 15).

Contract:
  * stdout carries the result - the canonical pin (mint), a status line, or, with
    --json, one machine-readable object;
  * stderr carries diagnostics only;
  * the process exit code is derived from the stable error code, never from the
    human message.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .config import Config
from .core import materialize, mint, verify
from .errors import EXIT_OK, EXIT_USAGE, PinError
from .pin import Pin
from .registry import Registry


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="hive-pin", description="One Hive R0 immutable git pinning")
    p.add_argument("--version", action="version", version=f"hive-pin {__version__}")
    p.add_argument("--registry", metavar="PATH",
                   help="registry JSON (default: $HIVEPIN_REPOSITORY_REGISTRY_PATH or ./registry.json)")
    p.add_argument("--config", metavar="PATH", help="config JSON (default: $HIVEPIN_CONFIG if set)")
    p.add_argument("--json", action="store_true", help="emit one machine-readable object on stdout")
    sub = p.add_subparsers(dest="command", required=True)

    m = sub.add_parser("mint", help="create a pin from a registered repository")
    m.add_argument("repository")
    m.add_argument("path", help="repository-relative path, or '.' for the whole tree")
    m.add_argument("--commit", metavar="REV",
                   help="pin this commit instead of the clean worktree HEAD")
    m.add_argument("--output", metavar="FILE", help="write the canonical pin atomically to FILE")
    m.add_argument("--offline", action="store_true",
                   help="skip the publication check (produces a non-authoritative pin)")

    v = sub.add_parser("verify", help="check that a pin still resolves to the same content")
    v.add_argument("pin", help="canonical pin JSON, a hivepin:v1: envelope, or - for stdin")
    v.add_argument("--offline", action="store_true", help="skip the publication check")

    x = sub.add_parser("materialize", help="extract a pin's content into DEST (must not exist)")
    x.add_argument("pin", help="canonical pin JSON, a hivepin:v1: envelope, or - for stdin")
    x.add_argument("dest")
    x.add_argument("--offline", action="store_true", help="skip the publication check")

    s = sub.add_parser("show", help="parse a pin and print its fields (no repository access)")
    s.add_argument("pin", help="canonical pin JSON, a hivepin:v1: envelope, or - for stdin")

    return p


def _load_pin(arg: str) -> Pin:
    text = sys.stdin.read() if arg == "-" else arg
    return Pin.parse(text)


def _emit(obj: dict, as_json: bool, *, human: str) -> None:
    if as_json:
        json.dump(obj, sys.stdout, ensure_ascii=False, sort_keys=True)
        sys.stdout.write("\n")
    else:
        print(human)


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        cfg = Config.load(args.config, repository_registry_path=args.registry)

        if args.command == "show":
            pin = _load_pin(args.pin)
            _emit(
                {"status": "ok", "operation": "show", "canonical": pin.to_canonical_dict(),
                 "envelope": pin.envelope, "label": pin.display_label},
                args.json,
                human=pin.to_canonical_bytes().decode("utf-8"),
            )
            return EXIT_OK

        registry = Registry.load(cfg.registry_file(args.registry))

        if args.command == "mint":
            res = mint(args.repository, args.path, registry,
                       commit=args.commit, offline=args.offline, config=cfg)
            if args.output:
                _atomic_write(args.output, res.pin.to_file_bytes())
                _emit({**res.to_dict(), "output": args.output}, args.json,
                      human=f"{res.pin.display_label}\t{args.output}")
            else:
                _emit(res.to_dict(), args.json,
                      human=res.pin.to_canonical_bytes().decode("utf-8"))
            return EXIT_OK

        if args.command == "verify":
            res = verify(_load_pin(args.pin), registry, offline=args.offline, config=cfg)
            _emit(res.to_dict(), args.json,
                  human=f"verified\t{res.pin.display_label}\tpublication={res.publication_status}")
            return EXIT_OK

        if args.command == "materialize":
            res = materialize(_load_pin(args.pin), args.dest, registry,
                              offline=args.offline, config=cfg)
            _emit(res.to_dict(), args.json, human=str(res.destination))
            return EXIT_OK

        return EXIT_USAGE
    except PinError as exc:
        if args.json:
            json.dump(exc.to_dict(), sys.stdout, ensure_ascii=False, sort_keys=True)
            sys.stdout.write("\n")
        else:
            print(f"hive-pin: error [{exc.code}]: {exc.message}", file=sys.stderr)
        return exc.exit_code
    except BrokenPipeError:
        return EXIT_OK


def _atomic_write(path: str, data: bytes) -> None:
    import contextlib
    import os
    import tempfile
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".hive-pin-")
    try:
        with open(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


if __name__ == "__main__":
    sys.exit(main())
