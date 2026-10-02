# SPDX-License-Identifier: GPL-3.0-or-later
"""SPEC 12.1, 13, 19.1-19.3 - mint."""

import pytest

from hivepin.errors import PinError
from hivepin.pin import Pin
from tests.conftest import git, v1_mint


def test_mint_file_default_worktree(scenario):
    r = v1_mint("workspace", "reports/result.md", scenario.registry(), config=scenario.config())
    p = r.pin
    assert p.kind == "file" and p.mode == "100644"
    assert p.commit_oid.startswith("sha1:") and len(p.commit_hex) == 40
    assert p.content_digest.startswith("sha256:")
    assert r.publication_status == "verified"


def test_mint_executable_mode_preserved(scenario):
    r = v1_mint("workspace", "bin/run.sh", scenario.registry(), config=scenario.config())
    assert r.pin.mode == "100755"


def test_mint_tree_and_whole_repo(scenario):
    r = v1_mint("workspace", "docs", scenario.registry(), config=scenario.config())
    assert r.pin.kind == "tree" and r.pin.mode is None
    whole = v1_mint("workspace", ".", scenario.registry(), config=scenario.config())
    assert whole.pin.kind == "tree"


def test_mint_is_deterministic_across_clones(scenario, tmp_path):
    clone = tmp_path / "clone2"
    git(scenario.tmp, "clone", "-q", str(scenario.remote), str(clone))
    reg2 = scenario.registry(local_path=str(clone))
    a = v1_mint("workspace", "reports/result.md", scenario.registry(), config=scenario.config())
    b = v1_mint("workspace", "reports/result.md", reg2, config=scenario.config())
    assert a.pin.to_canonical_bytes() == b.pin.to_canonical_bytes()

    ta = v1_mint("workspace", ".", scenario.registry(), config=scenario.config())
    tb = v1_mint("workspace", ".", reg2, config=scenario.config())
    assert ta.pin.to_canonical_bytes() == tb.pin.to_canonical_bytes()


def test_mint_refuses_dirty_tracked_file(scenario):
    (scenario.work / "reports/result.md").write_text("changed\n")
    with pytest.raises(PinError) as e:
        v1_mint("workspace", "reports/result.md", scenario.registry(), config=scenario.config())
    assert e.value.code == "DIRTY_PATH"


def test_mint_refuses_untracked_under_tree(scenario):
    (scenario.work / "docs/new.txt").write_text("x\n")
    with pytest.raises(PinError) as e:
        v1_mint("workspace", "docs", scenario.registry(), config=scenario.config())
    assert e.value.code == "DIRTY_PATH"


def test_mint_explicit_commit_ignores_worktree(scenario):
    head = git(scenario.work, "rev-parse", "HEAD").stdout.strip()
    (scenario.work / "reports/result.md").write_text("dirty\n")
    r = v1_mint("workspace", "reports/result.md", scenario.registry(),
             commit=head, config=scenario.config())
    assert r.pin.commit_hex == head


def test_mint_resolves_abbreviated_commit_to_full(scenario):
    short = git(scenario.work, "rev-parse", "--short=8", "HEAD").stdout.strip()
    r = v1_mint("workspace", "reports/result.md", scenario.registry(),
             commit=short, config=scenario.config())
    assert len(r.pin.commit_hex) == 40


def test_mint_refuses_unpublished_commit(scenario):
    scenario.write("reports/result.md", "local only\n")
    local_commit = scenario.commit_all("not pushed")
    with pytest.raises(PinError) as e:
        v1_mint("workspace", "reports/result.md", scenario.registry(), config=scenario.config())
    assert e.value.code == "COMMIT_NOT_PUBLISHED"
    # ... until it is published
    scenario.push()
    r = v1_mint("workspace", "reports/result.md", scenario.registry(), config=scenario.config())
    assert r.pin.commit_hex == local_commit


def test_mint_offline_marks_not_checked(scenario):
    scenario.write("reports/result.md", "local only\n")
    scenario.commit_all("not pushed")
    r = v1_mint("workspace", "reports/result.md", scenario.registry(),
             offline=True, config=scenario.config())
    assert r.publication_status == "not_checked"


def test_mint_branch_not_in_allowed_pattern_is_unpublished(scenario):
    scenario.write("reports/result.md", "on a side branch\n")
    git(scenario.work, "checkout", "-q", "-b", "feature/x")
    scenario.commit_all("side")
    scenario.push("feature/x")
    with pytest.raises(PinError) as e:
        v1_mint("workspace", "reports/result.md", scenario.registry(), config=scenario.config())
    assert e.value.code == "COMMIT_NOT_PUBLISHED"


def test_mint_rejects_symlink(scenario):
    (scenario.work / "link").symlink_to("reports/result.md")
    scenario.commit_all("add symlink")
    scenario.push()
    with pytest.raises(PinError) as e:
        v1_mint("workspace", "link", scenario.registry(), config=scenario.config())
    assert e.value.code == "UNSUPPORTED_OBJECT"


def test_mint_missing_path(scenario):
    with pytest.raises(PinError) as e:
        v1_mint("workspace", "nope/missing.md", scenario.registry(), config=scenario.config())
    assert e.value.code == "PATH_NOT_FOUND"


def test_mint_sha256_repo(sha256_scenario):
    r = v1_mint("workspace", "reports/result.md", sha256_scenario.registry(),
             config=sha256_scenario.config())
    assert r.pin.object_format == "sha256"
    assert len(r.pin.commit_hex) == 64
    assert Pin.parse(r.pin.envelope) == r.pin
