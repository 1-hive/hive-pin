# SPDX-License-Identifier: GPL-3.0-or-later
"""SPEC 16, 19.4, 19.7 - security requirements and non-interference."""

import os
import subprocess

import pytest

from hivepin.core import materialize, verify
from hivepin.errors import PinError
from hivepin.pin import Pin
from tests.conftest import git, v1_mint


def _snapshot(repo):
    def r(*a):
        return git(repo, *a).stdout.strip()
    return {
        "head": r("rev-parse", "HEAD"),
        "status": r("status", "--porcelain"),
        "branches": r("for-each-ref", "refs/heads"),
        "tags": r("for-each-ref", "refs/tags"),
        "stash": r("stash", "list"),
        "config_remote": r("remote", "-v"),
        "reflog": r("reflog", "-n", "5"),
    }


def test_operations_do_not_touch_the_caller_repo(scenario, tmp_path):
    before = _snapshot(scenario.work)
    pin = v1_mint("workspace", "docs", scenario.registry(), config=scenario.config()).pin
    verify(pin, scenario.registry(), config=scenario.config())
    materialize(pin, tmp_path / "out", scenario.registry(), config=scenario.config())
    assert _snapshot(scenario.work) == before


def test_hooks_are_not_executed(scenario, tmp_path):
    hook = scenario.work / ".git/hooks/post-checkout"
    hook.write_text("#!/bin/sh\ntouch " + str(tmp_path / "HOOK_RAN") + "\n")
    hook.chmod(0o755)
    for h in ("pre-command", "post-index-change", "reference-transaction"):
        p = scenario.work / ".git/hooks" / h
        p.write_text("#!/bin/sh\ntouch " + str(tmp_path / "HOOK_RAN") + "\n")
        p.chmod(0o755)
    pin = v1_mint("workspace", ".", scenario.registry(), config=scenario.config()).pin
    materialize(pin, tmp_path / "out", scenario.registry(), config=scenario.config())
    assert not (tmp_path / "HOOK_RAN").exists()


def test_path_traversal_in_pin_is_rejected_before_fs_access(scenario, tmp_path):
    pin = v1_mint("workspace", "reports/result.md", scenario.registry(), config=scenario.config()).pin
    for evil in ("../escape", "a/../../escape", "/abs/path"):
        with pytest.raises(PinError):
            Pin.parse(pin.to_canonical_bytes().decode().replace('"reports/result.md"', f'"{evil}"'))


def test_materialize_cannot_write_outside_destination(scenario, tmp_path, monkeypatch):
    pin = v1_mint("workspace", "docs", scenario.registry(), config=scenario.config()).pin
    import hivepin.core as core
    real = core._blob_entries

    def poisoned(*a, **k):
        entries = real(*a, **k)
        entries[0]["path"] = "../../pwned"
        return entries

    monkeypatch.setattr(core, "_blob_entries", poisoned)
    with pytest.raises(PinError) as e:
        materialize(pin, tmp_path / "out", scenario.registry(), config=scenario.config())
    assert e.value.code in ("PATH_COLLISION", "INVALID_PATH")
    assert not (tmp_path / "pwned").exists()
    assert not (tmp_path.parent / "pwned").exists()


def test_submodule_pin_rejected(scenario, tmp_path):
    # create a real gitlink entry
    sub = tmp_path / "sub"
    subprocess.run(["git", "init", "-q", str(sub)], check=True,
                   env={**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null"})
    (sub / "f").write_text("x")
    git(sub, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    git(sub, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "s")
    git(scenario.work, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(sub), "vendor/sub")
    scenario.commit_all("add submodule")
    scenario.push()
    with pytest.raises(PinError) as e:
        v1_mint("workspace", "vendor/sub", scenario.registry(), offline=True, config=scenario.config())
    assert e.value.code == "UNSUPPORTED_OBJECT"
    with pytest.raises(PinError) as e:
        v1_mint("workspace", ".", scenario.registry(), offline=True, config=scenario.config())
    assert e.value.code == "UNSUPPORTED_OBJECT"


def test_no_shell_interpolation_in_paths(scenario, tmp_path):
    scenario.write("weird/$(touch PWNED).txt", "x\n")
    scenario.write("weird/a;b|c.txt", "y\n")
    scenario.commit_all("weird names")
    scenario.push()
    pin = v1_mint("workspace", "weird", scenario.registry(), config=scenario.config()).pin
    materialize(pin, tmp_path / "out", scenario.registry(), config=scenario.config())
    assert not (tmp_path / "PWNED").exists()
    assert (tmp_path / "out" / "weird" / "$(touch PWNED).txt").read_text() == "x\n"


def test_file_size_cap_enforced(scenario, tmp_path):
    scenario.write("big.bin", b"\x00" * 4096)
    scenario.commit_all("big")
    scenario.push()
    cfg = scenario.config(max_file_bytes=1024)
    with pytest.raises(PinError) as e:
        v1_mint("workspace", "big.bin", scenario.registry(), config=cfg)
    assert e.value.code == "LIMIT_EXCEEDED"


def test_tree_file_count_cap_enforced(scenario):
    cfg = scenario.config(max_tree_files=1)
    with pytest.raises(PinError) as e:
        v1_mint("workspace", ".", scenario.registry(), config=cfg)
    assert e.value.code == "LIMIT_EXCEEDED"
