# SPDX-License-Identifier: GPL-3.0-or-later
"""SPEC 8, 9 - the Pin object, canonical bytes, labels, envelope."""

import pytest

from hivepin.canonical import sha256_hex
from hivepin.errors import PinError
from hivepin.pin import Pin


def file_pin(**over) -> Pin:
    kw = dict(repository="workspace", commit_oid="sha1:" + "a" * 40, path="reports/r.md",
              kind="file", object_oid="sha1:" + "b" * 40,
              content_digest=sha256_hex(b"hello"), mode="100644")
    kw.update(over)
    return Pin(**kw)


def test_canonical_bytes_are_sorted_and_have_no_newline():
    b = file_pin().to_canonical_bytes()
    assert b.startswith(b'{"commit_oid":')
    assert b"\n" not in b
    assert file_pin().to_file_bytes() == b + b"\n"


def test_envelope_roundtrip():
    p = file_pin()
    assert Pin.parse(p.envelope) == p


def test_canonical_bytes_roundtrip():
    p = file_pin()
    assert Pin.from_canonical_bytes(p.to_file_bytes()) == p
    assert Pin.parse(p.to_canonical_bytes().decode()) == p


def test_noncanonical_bytes_rejected():
    # valid content, but pretty-printed / reordered - not canonical
    bad = b'{"version": 1, "repository": "workspace"}'
    with pytest.raises(PinError):
        Pin.from_canonical_bytes(bad)


def test_unknown_field_rejected():
    d = file_pin().to_canonical_dict()
    d["extra"] = 1
    with pytest.raises(PinError) as e:
        Pin.from_dict(d)
    assert e.value.code == "INVALID_PIN"


def test_wrong_version_rejected():
    d = file_pin().to_canonical_dict()
    d["version"] = 2
    with pytest.raises(PinError) as e:
        Pin.from_dict(d)
    assert e.value.code == "UNSUPPORTED_VERSION"


def test_tree_pin_must_not_have_mode():
    with pytest.raises(PinError):
        Pin(repository="c", commit_oid="sha1:" + "a" * 40, path=".", kind="tree",
            object_oid="sha1:" + "b" * 40, content_digest=sha256_hex(b"x"), mode="100644")


def test_file_pin_requires_valid_mode():
    with pytest.raises(PinError):
        file_pin(mode=None)
    with pytest.raises(PinError):
        file_pin(mode="100600")


def test_mixed_oid_formats_rejected():
    with pytest.raises(PinError) as e:
        file_pin(commit_oid="sha1:" + "a" * 40, object_oid="sha256:" + "b" * 64)
    assert e.value.code == "OBJECT_FORMAT_MISMATCH"


def test_display_label_is_short_and_marked_nonauthoritative():
    p = file_pin()
    assert p.display_label == "workspace:reports/r.md@aaaaaaaa"
    # the label is not accepted back as a pin
    with pytest.raises(PinError):
        Pin.parse(p.display_label)


def test_abbreviated_or_bare_sha_rejected():
    with pytest.raises(PinError):
        file_pin(commit_oid="sha1:abc1234")
    with pytest.raises(PinError):
        file_pin(commit_oid="a" * 40)
