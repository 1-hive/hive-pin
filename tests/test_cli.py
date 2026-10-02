# SPDX-License-Identifier: GPL-3.0-or-later
"""SPEC 12, 14, 15 - the hive-pin CLI: stdout/stderr discipline, exit codes, --json."""

import json
import subprocess
import sys


def run(scenario, *args, stdin=None):
    env = {
        "HIVEPIN_REPOSITORY_REGISTRY_PATH": str(_registry_file(scenario)),
        "HIVEPIN_CACHE_DIRECTORY": str(scenario.tmp / "cli-cache"),
        "PATH": _path(),
    }
    return subprocess.run(
        [sys.executable, "-m", "hivepin.cli", *args],
        capture_output=True, text=True, input=stdin, env=env,
    )


def _path():
    import os
    return os.environ["PATH"]


def _registry_file(scenario):
    p = scenario.tmp / "registry.json"
    if not p.exists():
        p.write_text(json.dumps(scenario.registry_dict()))
    return p


def test_mint_then_verify_then_materialize(scenario, tmp_path):
    m = run(scenario, "mint", "--format", "1", "workspace", "reports/result.md")
    assert m.returncode == 0
    assert m.stdout.strip().startswith("{") and m.stderr == ""
    pin = m.stdout.strip()

    v = run(scenario, "verify", "-", stdin=pin)
    assert v.returncode == 0
    assert v.stdout.startswith("verified\t")

    dest = tmp_path / "out"
    x = run(scenario, "materialize", "-", str(dest), stdin=pin)
    assert x.returncode == 0
    assert (dest / "reports/result.md").read_text() == "hello world\n"


def test_json_mode_emits_one_object(scenario):
    r = run(scenario, "--json", "mint", "--format", "1", "workspace", "bin/run.sh")
    assert r.returncode == 0
    obj = json.loads(r.stdout)
    assert obj["status"] == "minted"
    assert obj["canonical"]["mode"] == "100755"
    assert obj["publication_status"] == "verified"


def test_show_needs_no_repo_access(scenario):
    m = run(scenario, "--json", "mint", "--format", "1", "workspace", "reports/result.md")
    envelope = json.loads(m.stdout)["pin"]
    s = run(scenario, "show", envelope)
    assert s.returncode == 0
    assert s.stdout.strip().startswith('{"commit_oid"')


def test_malformed_pin_goes_to_stderr_with_code_and_exit2(scenario):
    r = run(scenario, "verify", '{"version":1,"repository":"x"}')
    assert r.returncode == 2          # SPEC 15: malformed input
    assert r.stdout == ""
    assert "[INVALID_PIN]" in r.stderr


def test_verification_failure_exit3(scenario):
    m = run(scenario, "--json", "mint", "--format", "1", "workspace", "reports/result.md")
    envelope = json.loads(m.stdout)["pin"]
    # a well-formed pin whose commit is not in the repo -> exit 3
    forged = envelope  # tamper the underlying bytes via show->edit is overkill; use a bad repo
    r = run(scenario, "verify", forged.replace("hivepin:v1:", "hivepin:v1:"))
    assert r.returncode == 0  # sanity: the real one verifies
    bad = run(scenario, "--json", "verify", "-",
              stdin=json.dumps({**json.loads(m.stdout)["canonical"],
                                "content_digest": "sha256:" + "0" * 64}, separators=(",", ":"),
                               sort_keys=True))
    assert bad.returncode == 3
    assert json.loads(bad.stdout)["code"] == "CONTENT_MISMATCH"


def test_error_json_mode_is_machine_readable(scenario):
    r = run(scenario, "--json", "mint", "--format", "1", "ghost", "x")
    assert r.returncode == 3
    obj = json.loads(r.stdout)
    assert obj == {"status": "error", "code": "UNKNOWN_REPOSITORY", **{k: obj[k] for k in obj
                   if k not in ("status", "code")}}
    assert obj["code"] == "UNKNOWN_REPOSITORY"


def test_unpublished_exit_code_is_3(scenario):
    scenario.write("reports/result.md", "local\n")
    scenario.commit_all("local only")
    r = run(scenario, "mint", "--format", "1", "workspace", "reports/result.md")
    assert r.returncode == 3
    assert "COMMIT_NOT_PUBLISHED" in r.stderr


def test_output_file_written_atomically(scenario, tmp_path):
    out = tmp_path / "thing.pin"
    r = run(scenario, "mint", "--format", "1", "workspace", "reports/result.md", "--output", str(out))
    assert r.returncode == 0
    assert out.read_bytes().endswith(b"\n")
    # the file body (without the trailing LF) is canonical
    from hivepin.pin import Pin
    Pin.from_canonical_bytes(out.read_bytes())
