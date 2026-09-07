# SPDX-License-Identifier: GPL-3.0-or-later
"""SPEC 8, 10, 11 - canonical encodings and validation (pure, no git)."""

import unicodedata

import pytest

from hivepin.canonical import (
    build_manifest,
    canonical_json_bytes,
    check_oid,
    check_path,
    check_repository_id,
    manifest_digest,
    sha256_hex,
)
from hivepin.errors import PinError

E_ACUTE_NFC = unicodedata.normalize("NFC", "é")   # precomposed
E_ACUTE_NFD = unicodedata.normalize("NFD", "é")   # e + combining acute


def test_canonical_json_is_sorted_compact_utf8():
    b = canonical_json_bytes({"b": 1, "a": "x", "z": 2})
    assert b == b'{"a":"x","b":1,"z":2}'
    assert b"\n" not in b and b" " not in b


def test_canonical_json_keeps_non_ascii_literal():
    b = canonical_json_bytes({"k": E_ACUTE_NFC})
    assert b == ('{"k":"' + E_ACUTE_NFC + '"}').encode("utf-8")


def test_canonical_json_rejects_nan():
    with pytest.raises(ValueError):
        canonical_json_bytes({"x": float("nan")})


@pytest.mark.parametrize("ok", ["a", "a-b", "repo.1", "x" * 64, "0abc_def-1.2"])
def test_repo_id_accepts(ok):
    assert check_repository_id(ok) == ok


@pytest.mark.parametrize("bad", ["", "-x", ".x", "A", "a/b", "x" * 65, "a:b", "w" + E_ACUTE_NFC])
def test_repo_id_rejects(bad):
    with pytest.raises(PinError):
        check_repository_id(bad)


@pytest.mark.parametrize("ok", ["a", "a/b/c", ".", "name with space", "@weird:name",
                                "r" + E_ACUTE_NFC + "serve"])
def test_path_accepts(ok):
    assert check_path(ok) == ok


@pytest.mark.parametrize("bad", [
    "", "/abs", "a/../b", "./a", "a/./b", "a//b", "a/", "a\\b", "a\nb", "a\x00b",
])
def test_path_rejects(bad):
    with pytest.raises(PinError) as e:
        check_path(bad)
    assert e.value.code == "INVALID_PATH"


def test_path_rejects_non_nfc():
    assert E_ACUTE_NFD != E_ACUTE_NFC
    with pytest.raises(PinError):
        check_path("dir/" + E_ACUTE_NFD)


def test_oid_prefix_must_match_format():
    check_oid("sha1:" + "a" * 40, object_format="sha1", field="commit_oid")
    with pytest.raises(PinError) as e:
        check_oid("sha256:" + "a" * 64, object_format="sha1", field="commit_oid")
    assert e.value.code == "OBJECT_FORMAT_MISMATCH"


@pytest.mark.parametrize("bad", ["a" * 40, "sha1:AAAA", "sha1:" + "a" * 39, "sha1:xyz"])
def test_oid_rejects_malformed(bad):
    with pytest.raises(PinError):
        check_oid(bad, object_format="sha1", field="commit_oid")


def test_manifest_is_bytewise_sorted_with_trailing_lf_per_line():
    entries = [
        {"path": "b.txt", "mode": "100644", "size": 1, "digest": sha256_hex(b"x")},
        {"path": "a.txt", "mode": "100644", "size": 1, "digest": sha256_hex(b"y")},
        {"path": "a/deep", "mode": "100755", "size": 2, "digest": sha256_hex(b"zz")},
    ]
    m = build_manifest(entries).decode("utf-8")
    lines = m.splitlines()
    assert [ln.split('"path":"')[1].split('"')[0] for ln in lines] == ["a.txt", "a/deep", "b.txt"]
    assert m.endswith("\n")
    assert build_manifest(list(reversed(entries))) == build_manifest(entries)


def test_manifest_rejects_duplicate_paths():
    d = sha256_hex(b"x")
    with pytest.raises(PinError) as e:
        build_manifest([
            {"path": "a", "mode": "100644", "size": 1, "digest": d},
            {"path": "a", "mode": "100644", "size": 1, "digest": d},
        ])
    assert e.value.code == "PATH_COLLISION"


def test_manifest_digest_stable():
    entries = [{"path": "x", "mode": "100644", "size": 3, "digest": sha256_hex(b"abc")}]
    assert manifest_digest(entries) == manifest_digest(list(entries))
