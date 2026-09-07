# SPDX-License-Identifier: GPL-3.0-or-later
"""SPEC 12.2, 19.2, 19.6 - verify, including tampering detection."""

import dataclasses

import pytest

from hivepin.core import mint, verify
from hivepin.errors import PinError
from hivepin.pin import Pin
from tests.conftest import git


def minted(scenario, path="reports/result.md"):
    return mint("workspace", path, scenario.registry(), config=scenario.config()).pin


def test_verify_roundtrip_file_and_tree(scenario):
    for path in ("reports/result.md", "bin/run.sh", "docs", "."):
        pin = minted(scenario, path)
        r = verify(pin, scenario.registry(), config=scenario.config())
        assert r.publication_status == "verified"


def test_verify_offline_reports_not_checked(scenario):
    pin = minted(scenario)
    r = verify(pin, scenario.registry(), offline=True, config=scenario.config())
    assert r.publication_status == "not_checked"


def test_verify_survives_branch_movement(scenario):
    pin = minted(scenario)
    scenario.write("reports/result.md", "a later change\n")
    scenario.commit_all("move main forward")
    scenario.push()
    verify(pin, scenario.registry(), config=scenario.config())  # still an ancestor of main


def test_verify_survives_rename_and_delete(scenario):
    pin = minted(scenario)
    git(scenario.work, "mv", "reports/result.md", "reports/renamed.md")
    scenario.commit_all("rename")
    git(scenario.work, "rm", "-q", "reports/renamed.md")
    scenario.commit_all("delete")
    scenario.push()
    verify(pin, scenario.registry(), config=scenario.config())


@pytest.mark.parametrize("field,value,codes", [
    ("commit_oid", "sha1:" + "0" * 40, ("COMMIT_NOT_FOUND", "COMMIT_NOT_PUBLISHED")),
    ("object_oid", "sha1:" + "0" * 40, ("OBJECT_MISMATCH",)),
    ("path", "reports/absent.md", ("PATH_NOT_FOUND",)),
    ("content_digest", "sha256:" + "0" * 64, ("CONTENT_MISMATCH",)),
    ("mode", "100755", ("MODE_MISMATCH",)),
])
def test_verify_detects_field_tampering(scenario, field, value, codes):
    pin = minted(scenario)
    tampered = dataclasses.replace(pin, **{field: value})
    with pytest.raises(PinError) as e:
        verify(tampered, scenario.registry(), config=scenario.config())
    assert e.value.code in codes


def test_verify_offline_bogus_commit_is_not_found(scenario):
    pin = dataclasses.replace(minted(scenario), commit_oid="sha1:" + "0" * 40)
    with pytest.raises(PinError) as e:
        verify(pin, scenario.registry(), offline=True, config=scenario.config())
    assert e.value.code == "COMMIT_NOT_FOUND"


def test_verify_detects_kind_tampering(scenario):
    """A file pin re-labelled as a tree (bytes edited in storage) fails KIND_MISMATCH."""
    pin = minted(scenario)
    raw = pin.to_canonical_bytes().decode()
    forged = raw.replace('"kind":"file"', '"kind":"tree"').replace(',"mode":"100644"', "")
    tampered = Pin.from_canonical_bytes(forged.encode())  # structurally valid tree pin
    with pytest.raises(PinError) as e:
        verify(tampered, scenario.registry(), config=scenario.config())
    assert e.value.code == "KIND_MISMATCH"


def test_verify_detects_cache_object_substitution(scenario):
    """If the local clone's blob bytes are swapped under a matching path but a
    different object, OBJECT_MISMATCH / CONTENT_MISMATCH fires."""
    pin = minted(scenario)
    # rewrite history so reports/result.md at the *same tree position* differs
    scenario.write("reports/result.md", "SUBSTITUTED\n")
    git(scenario.work, "commit", "-aq", "--amend", "--no-edit")
    git(scenario.work, "push", "-qf", "origin", "main")
    with pytest.raises(PinError) as e:
        verify(pin, scenario.registry(), config=scenario.config())
    assert e.value.code in ("COMMIT_NOT_FOUND", "COMMIT_NOT_PUBLISHED", "OBJECT_MISMATCH",
                            "CONTENT_MISMATCH")


def test_verify_unknown_repo(scenario):
    pin = minted(scenario)
    bad = dataclasses.replace(pin, repository="ghost")
    with pytest.raises(PinError) as e:
        verify(bad, scenario.registry(), config=scenario.config())
    assert e.value.code == "UNKNOWN_REPOSITORY"


def test_verify_offline_without_cache_is_commit_not_found(scenario, tmp_path):
    pin = minted(scenario)
    # a registry whose local clone does not contain the commit and empty cache
    empty = tmp_path / "empty"
    git(scenario.tmp, "init", "-q", str(empty))
    reg = scenario.registry(local_path=str(empty))
    with pytest.raises(PinError) as e:
        verify(pin, reg, offline=True, config=scenario.config(cache_directory=str(tmp_path / "c2")))
    assert e.value.code == "COMMIT_NOT_FOUND"


def test_verify_from_cache_when_local_missing(scenario, tmp_path):
    pin = minted(scenario)
    empty = tmp_path / "empty2"
    git(scenario.tmp, "init", "-q", str(empty))
    reg = scenario.registry(local_path=str(empty))
    # online: publication check populates the cache, verify then reads objects from it
    r = verify(pin, reg, config=scenario.config())
    assert r.publication_status == "verified"
