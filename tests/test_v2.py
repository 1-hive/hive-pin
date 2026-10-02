# SPDX-License-Identifier: GPL-3.0-or-later
"""Pin format v2 (docs/pin-v2.md): commit pins, re-hash integrity, safe symlinks."""

import json
import os
import subprocess
import sys
import zlib

import pytest

from hivepin.core import materialize, mint, verify
from hivepin.errors import PinError
from hivepin.pin import Pin, PinV2, parse_pin, pin_from_dict
from tests.conftest import git, v1_mint


def m2(scenario, path=None, **kw):
    return mint("workspace", path, scenario.registry(), config=scenario.config(), **kw).pin


def mat(scenario, pin, dest):
    return materialize(pin, dest, scenario.registry(), config=scenario.config())


def head(scenario):
    return git(scenario.work, "rev-parse", "HEAD").stdout.strip()


# --------------------------------------------------------------------------- pin
def test_whole_commit_pin_is_three_fields(scenario):
    pin = m2(scenario)
    assert pin.to_canonical_dict() == {"version": 2, "repository": "workspace",
                                       "commit_oid": f"sha1:{head(scenario)}"}
    assert pin.display_label == f"workspace@{head(scenario)[:8]}"


def test_dot_means_whole_commit_on_input(scenario):
    assert m2(scenario, ".") == m2(scenario)


def test_path_pin_and_label(scenario):
    pin = m2(scenario, "reports/result.md")
    assert pin.path == "reports/result.md"
    assert pin.display_label == f"workspace:reports/result.md@{head(scenario)[:8]}"


def test_deterministic_across_clones(scenario, tmp_path):
    clone = tmp_path / "clone"
    git(tmp_path, "clone", "-q", str(scenario.remote), str(clone))
    reg2 = scenario.registry(local_path=str(clone))
    a = m2(scenario)
    b = mint("workspace", None, reg2, config=scenario.config()).pin
    assert a.to_canonical_bytes() == b.to_canonical_bytes()


def test_parse_round_trip_and_envelopes(scenario):
    pin = m2(scenario, "docs")
    assert parse_pin(pin.to_canonical_bytes().decode()) == pin
    assert parse_pin(pin.envelope) == pin and pin.envelope.startswith("hivepin:v2:")
    v1 = v1_mint("workspace", "docs", scenario.registry(), config=scenario.config()).pin
    assert isinstance(parse_pin(v1.envelope), Pin)


@pytest.mark.parametrize("bad, code", [
    ({"path": "."}, "INVALID_PATH"),
    ({"path": "../x"}, "INVALID_PATH"),
    ({"kind": "tree"}, "INVALID_PIN"),
    ({"path": 3}, "INVALID_PIN"),
])
def test_invalid_v2_pins(bad, code):
    d = {"version": 2, "repository": "workspace", "commit_oid": "sha1:" + "a" * 40, **bad}
    with pytest.raises(PinError) as e:
        pin_from_dict(d)
    assert e.value.code == code


def test_non_canonical_bytes_refused():
    with pytest.raises(PinError) as e:
        parse_pin('{"version":2,"repository":"workspace","commit_oid":"sha1:' + "a" * 40 + '"}')
    assert e.value.code == "INVALID_PIN"


def test_unknown_version_refused():
    with pytest.raises(PinError) as e:
        pin_from_dict({"version": 3})
    assert e.value.code == "UNSUPPORTED_VERSION"


# -------------------------------------------------------------------------- mint
def test_mint_refuses_dirty_worktree_anywhere(scenario):
    scenario.write("stray/config.py", "x = 1\n")
    with pytest.raises(PinError) as e:
        m2(scenario)
    assert e.value.code == "DIRTY_PATH"
    m2(scenario, "reports/result.md")          # a path pin only checks its path


def test_mint_refuses_unpublished_commit(scenario):
    scenario.write("new.txt", "n\n")
    scenario.commit_all("local only")
    with pytest.raises(PinError) as e:
        m2(scenario)
    assert e.value.code == "COMMIT_NOT_PUBLISHED"


def test_mint_explicit_commit(scenario):
    first = head(scenario)
    scenario.write("later.txt", "l\n")
    scenario.commit_all("later")
    scenario.push()
    assert m2(scenario, commit=first).commit_hex == first


def test_mint_path_not_found(scenario):
    with pytest.raises(PinError) as e:
        m2(scenario, "nope.md")
    assert e.value.code == "PATH_NOT_FOUND"


def test_mint_sha256(sha256_scenario):
    pin = m2(sha256_scenario)
    assert pin.commit_oid.startswith("sha256:") and len(pin.commit_hex) == 64
    verify(pin, sha256_scenario.registry(), config=sha256_scenario.config())


# ------------------------------------------------------------------------ verify
def test_verify_reports_kind(scenario):
    reg, cfg = scenario.registry(), scenario.config()
    assert verify(m2(scenario), reg, config=cfg).kind == "commit"
    assert verify(m2(scenario, "docs"), reg, config=cfg).kind == "tree"
    assert verify(m2(scenario, "bin/run.sh"), reg, config=cfg).kind == "file"


def test_verify_unknown_commit(scenario):
    pin = PinV2("workspace", "sha1:" + "1" * 40)
    with pytest.raises(PinError) as e:
        verify(pin, scenario.registry(), config=scenario.config())
    assert e.value.code in ("COMMIT_NOT_PUBLISHED", "COMMIT_NOT_FOUND")


def _tamper_loose(repo, oid, typ, payload: bytes):
    path = repo / ".git" / "objects" / oid[:2] / oid[2:]
    assert path.exists(), "expected a loose object"
    path.chmod(0o644)
    path.write_bytes(zlib.compress(f"{typ} {len(payload)}\x00".encode() + payload))


def test_verify_detects_tampered_blob_on_the_path(scenario):
    pin = m2(scenario, "reports/result.md")
    blob = git(scenario.work, "rev-parse", "HEAD:reports/result.md").stdout.strip()
    _tamper_loose(scenario.work, blob, "blob", b"evil\n")
    with pytest.raises(PinError) as e:
        verify(pin, scenario.registry(), offline=True, config=scenario.config())
    assert e.value.code == "OBJECT_MISMATCH"


def test_materialize_detects_tampered_blob_anywhere(scenario, tmp_path):
    pin = m2(scenario)
    verify(pin, scenario.registry(), offline=True, config=scenario.config())
    blob = git(scenario.work, "rev-parse", "HEAD:docs/nested/deep.txt").stdout.strip()
    _tamper_loose(scenario.work, blob, "blob", b"evil\n")
    # verify only walks to the path (the root here), so it still passes...
    verify(pin, scenario.registry(), offline=True, config=scenario.config())
    # ...but materialize re-hashes every object it writes
    with pytest.raises(PinError) as e:
        materialize(pin, tmp_path / "out", scenario.registry(), offline=True, config=scenario.config())
    assert e.value.code == "OBJECT_MISMATCH"
    assert not (tmp_path / "out").exists()


def test_verify_detects_tampered_commit(scenario):
    pin = m2(scenario)
    raw = git(scenario.work, "cat-file", "commit", "HEAD").stdout.encode()
    _tamper_loose(scenario.work, head(scenario), "commit", raw.replace(b"seed", b"evil"))
    # git refuses the tampered commit itself, so the clean publication cache is used
    verify(pin, scenario.registry(), offline=True, config=scenario.config())
    # with no clean copy anywhere, the tampered one is never accepted
    import shutil
    shutil.rmtree(scenario.tmp / "cache")
    with pytest.raises(PinError) as e:
        verify(pin, scenario.registry(), offline=True, config=scenario.config())
    assert e.value.code in ("OBJECT_MISMATCH", "COMMIT_NOT_FOUND")


# ------------------------------------------------------------------- materialize
def test_materialize_whole_commit(scenario, tmp_path):
    res = mat(scenario, m2(scenario), tmp_path / "out")
    out = tmp_path / "out"
    assert (out / "reports/result.md").read_text() == "hello world\n"
    assert (out / "data/bytes.bin").read_bytes() == bytes(range(256))
    assert os.access(out / "bin/run.sh", os.X_OK)
    assert res.omitted == ()


def test_materialize_path_keeps_location(scenario, tmp_path):
    mat(scenario, m2(scenario, "docs"), tmp_path / "t")
    assert (tmp_path / "t/docs/nested/deep.txt").read_text() == "deep\n"
    assert not (tmp_path / "t/reports").exists()
    mat(scenario, m2(scenario, "reports/result.md"), tmp_path / "f")
    assert (tmp_path / "f/reports/result.md").read_text() == "hello world\n"


def test_materialize_refuses_existing_destination(scenario, tmp_path):
    (tmp_path / "out").mkdir()
    with pytest.raises(PinError) as e:
        mat(scenario, m2(scenario), tmp_path / "out")
    assert e.value.code == "DESTINATION_EXISTS"


def test_materialize_file_cap(scenario, tmp_path):
    with pytest.raises(PinError) as e:
        materialize(m2(scenario), tmp_path / "out", scenario.registry(),
                    config=scenario.config(max_tree_files=2))
    assert e.value.code == "LIMIT_EXCEEDED"
    assert not (tmp_path / "out").exists()


# ----------------------------------------------------------------------- symlinks
def _links(scenario, links: dict):
    for rel, target in links.items():
        p = scenario.work / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(target, p)
    scenario.commit_all("links")
    scenario.push()


def test_symlinks_inside_are_created_others_omitted(scenario, tmp_path):
    _links(scenario, {
        "docs/latest": "nested",                  # in-tree dir
        "reports/alias.md": "result.md",          # in-tree file
        "abs": "/etc/passwd",                     # absolute
        "esc": "../outside",                      # escapes
        "d/l": "..",                              # resolves to the root: fine
        "trick": "d/l/../x",                      # looks inside, resolves outside
        "loop1": "loop2", "loop2": "loop1",       # loop: never resolves
        "via": "esc/x",                           # through an escaping link
    })
    res = mat(scenario, m2(scenario), tmp_path / "out")
    out = tmp_path / "out"
    assert os.readlink(out / "docs/latest") == "nested"
    assert (out / "docs/latest/deep.txt").read_text() == "deep\n"
    assert (out / "reports/alias.md").read_text() == "hello world\n"
    assert os.readlink(out / "d/l") == ".."
    omitted = {o["path"]: o["reason"] for o in res.omitted}
    assert omitted == {p: "symlink_target" for p in ("abs", "esc", "trick", "loop1", "loop2", "via")}
    for p in omitted:
        assert not os.path.lexists(out / p)


def test_symlinks_resolve_within_the_pinned_subtree(scenario, tmp_path):
    _links(scenario, {"docs/up": "../reports/result.md", "docs/in": "nested/deep.txt"})
    res = mat(scenario, m2(scenario, "docs"), tmp_path / "out")
    assert {o["path"] for o in res.omitted} == {"docs/up"}
    assert (tmp_path / "out/docs/in").read_text() == "deep\n"


def test_pinned_path_that_is_a_symlink_is_refused(scenario):
    _links(scenario, {"link.md": "reports/result.md"})
    with pytest.raises(PinError) as e:
        m2(scenario, "link.md")
    assert e.value.code == "UNSUPPORTED_OBJECT"


def test_submodule_is_omitted_and_listed(scenario, tmp_path):
    sub = "a" * 40
    git(scenario.work, "update-index", "--add", "--cacheinfo", f"160000,{sub},vendor/lib")
    git(scenario.work, "commit", "-q", "-m", "submodule")
    scenario.push()
    res = mat(scenario, m2(scenario, commit="HEAD"), tmp_path / "out")
    assert list(res.omitted) == [{"path": "vendor/lib", "kind": "submodule",
                                  "reason": "submodule", "commit_oid": f"sha1:{sub}"}]
    assert (tmp_path / "out/reports/result.md").exists()


def test_dot_git_entry_is_refused(scenario, tmp_path):
    w = scenario.work
    blob = subprocess.run(["git", "-C", str(w), "hash-object", "-w", "--stdin"], input="[core]\n",
                       capture_output=True, text=True, check=True).stdout.strip()
    inner = subprocess.run(["git", "-C", str(w), "mktree"], input=f"100644 blob {blob}\tconfig\n",
                           capture_output=True, text=True, check=True).stdout.strip()
    root = subprocess.run(["git", "-C", str(w), "mktree"], input=f"040000 tree {inner}\t.git\n",
                          capture_output=True, text=True, check=True).stdout.strip()
    commit = git(w, "commit-tree", root, "-p", "HEAD", "-m", "dotgit").stdout.strip()
    git(w, "push", "-q", "origin", f"{commit}:refs/heads/main")
    pin = m2(scenario, commit=commit)
    with pytest.raises(PinError) as e:
        mat(scenario, pin, tmp_path / "out")
    assert e.value.code == "INVALID_PATH"
    assert not (tmp_path / "out").exists()


# ------------------------------------------------------------------ compatibility
def test_v1_pins_still_verify_and_materialize(scenario, tmp_path):
    pin = v1_mint("workspace", "reports/result.md", scenario.registry(), config=scenario.config()).pin
    assert isinstance(pin, Pin) and pin.content_digest.startswith("sha256:")
    verify(pin, scenario.registry(), config=scenario.config())
    materialize(pin, tmp_path / "v1", scenario.registry(), config=scenario.config())
    assert (tmp_path / "v1/reports/result.md").read_text() == "hello world\n"


def test_v1_mint_needs_a_path(scenario):
    with pytest.raises(PinError) as e:
        v1_mint("workspace", None, scenario.registry(), config=scenario.config())
    assert e.value.code == "INVALID_PATH"


# ---------------------------------------------------------------------------- CLI
def _cli(scenario, *args, stdin=None):
    reg = scenario.tmp / "registry.json"
    reg.write_text(json.dumps(scenario.registry_dict()))
    env = {"HIVEPIN_REPOSITORY_REGISTRY_PATH": str(reg),
           "HIVEPIN_CACHE_DIRECTORY": str(scenario.tmp / "cli-cache"), "PATH": os.environ["PATH"]}
    return subprocess.run([sys.executable, "-m", "hivepin.cli", *args],
                          capture_output=True, text=True, input=stdin, env=env)


def test_cli_v2_round_trip(scenario, tmp_path):
    m = _cli(scenario, "mint", "workspace")
    assert m.returncode == 0, m.stderr
    pin = m.stdout.strip()
    assert json.loads(pin) == {"version": 2, "repository": "workspace",
                               "commit_oid": f"sha1:{head(scenario)}"}
    v = _cli(scenario, "--json", "verify", "-", stdin=pin)
    assert v.returncode == 0 and json.loads(v.stdout)["kind"] == "commit"
    x = _cli(scenario, "--json", "materialize", "-", str(tmp_path / "o"), stdin=pin)
    assert x.returncode == 0 and json.loads(x.stdout)["omitted"] == []
    assert (tmp_path / "o/reports/result.md").exists()


def test_cli_format_1(scenario):
    m = _cli(scenario, "mint", "--format", "1", "workspace", "docs")
    assert m.returncode == 0 and json.loads(m.stdout)["kind"] == "tree"
    bad = _cli(scenario, "mint", "--format", "1", "workspace")
    assert bad.returncode == 2
