# SPDX-License-Identifier: GPL-3.0-or-later
"""SPEC 12.3, 19.1, 19.4, 19.5 - materialize."""

import os

import pytest

from hivepin.core import materialize
from hivepin.errors import PinError
from tests.conftest import git, v1_mint


def minted(scenario, path):
    return v1_mint("workspace", path, scenario.registry(), config=scenario.config()).pin


def test_materialize_file_preserves_path_and_bytes(scenario, tmp_path):
    pin = minted(scenario, "reports/result.md")
    dest = tmp_path / "out"
    materialize(pin, dest, scenario.registry(), config=scenario.config())
    assert (dest / "reports/result.md").read_text() == "hello world\n"


def test_materialize_preserves_executable_bit(scenario, tmp_path):
    pin = minted(scenario, "bin/run.sh")
    dest = tmp_path / "out"
    materialize(pin, dest, scenario.registry(), config=scenario.config())
    assert os.access(dest / "bin/run.sh", os.X_OK)


def test_materialize_binary_and_no_trailing_newline(scenario, tmp_path):
    scenario.write("data/x.bin", bytes(range(256)))
    scenario.write("data/noeol", "no newline here")
    scenario.commit_all("binary")
    scenario.push()
    for path, dest in (("data/x.bin", "b1"), ("data/noeol", "b2")):
        pin = minted(scenario, path)
        out = tmp_path / dest
        materialize(pin, out, scenario.registry(), config=scenario.config())
    assert (tmp_path / "b1" / "data/x.bin").read_bytes() == bytes(range(256))
    assert (tmp_path / "b2" / "data/noeol").read_text() == "no newline here"


def test_materialize_tree_subtree(scenario, tmp_path):
    pin = minted(scenario, "docs")
    dest = tmp_path / "tree"
    materialize(pin, dest, scenario.registry(), config=scenario.config())
    assert (dest / "docs/nested/deep.txt").read_text() == "deep\n"


def test_materialize_whole_repo(scenario, tmp_path):
    pin = minted(scenario, ".")
    dest = tmp_path / "whole"
    materialize(pin, dest, scenario.registry(), config=scenario.config())
    assert (dest / "reports/result.md").exists()
    assert (dest / "bin/run.sh").exists()


def test_materialize_refuses_existing_destination(scenario, tmp_path):
    pin = minted(scenario, "reports/result.md")
    dest = tmp_path / "exists"
    dest.mkdir()
    with pytest.raises(PinError) as e:
        materialize(pin, dest, scenario.registry(), config=scenario.config())
    assert e.value.code == "DESTINATION_EXISTS"


def test_failed_materialize_leaves_no_partial_destination(scenario, tmp_path, monkeypatch):
    pin = minted(scenario, "docs")
    dest = tmp_path / "out"

    import hivepin.core as core
    real = core._blob_entries
    calls = {"n": 0}

    def boom(*a, **k):
        calls["n"] += 1
        if calls["n"] >= 2:
            raise PinError("INTERNAL_ERROR", "boom")
        return real(*a, **k)

    monkeypatch.setattr(core, "_blob_entries", boom)
    with pytest.raises(PinError):
        materialize(pin, dest, scenario.registry(), config=scenario.config())
    assert not dest.exists()
    assert not list(tmp_path.glob(".hivepin-*"))


def test_tree_with_symlink_is_rejected(scenario, tmp_path):
    (scenario.work / "docs/evil").symlink_to("/etc/passwd")
    scenario.commit_all("symlink in tree")
    scenario.push()
    # mint refuses it
    with pytest.raises(PinError) as e:
        v1_mint("workspace", "docs", scenario.registry(), offline=True, config=scenario.config())
    assert e.value.code == "UNSUPPORTED_OBJECT"
    # and the extraction primitive refuses it directly
    import hivepin.core as core
    head = git(scenario.work, "rev-parse", "HEAD").stdout.strip()
    with pytest.raises(PinError) as e:
        core._blob_entries(scenario.work, head, "docs", scenario.config())
    assert e.value.code == "UNSUPPORTED_OBJECT"


def test_materialize_offline_from_cache(scenario, tmp_path):
    pin = minted(scenario, "reports/result.md")
    # prime cache via an online verify, then go offline
    from hivepin.core import verify
    verify(pin, scenario.registry(), config=scenario.config())
    materialize(pin, tmp_path / "o", scenario.registry(), offline=True, config=scenario.config())
    assert (tmp_path / "o" / "reports/result.md").exists()
