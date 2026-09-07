# SPDX-License-Identifier: GPL-3.0-or-later
"""SPEC 7 - repository registry."""

import json

import pytest

from hivepin.errors import PinError
from hivepin.registry import Registry


def base(**repo):
    r = {"fetch_urls": ["https://example.invalid/x.git"]}
    r.update(repo)
    return {"version": 1, "repositories": {"workspace": r}}


def test_loads_and_resolves():
    reg = Registry.from_dict(base())
    repo = reg.get("workspace")
    assert repo.id == "workspace"
    assert repo.object_format == "sha1"
    assert repo.allowed_ref_patterns == ("refs/heads/*", "refs/tags/*")


def test_unknown_repo_id_is_typed_error():
    reg = Registry.from_dict(base())
    with pytest.raises(PinError) as e:
        reg.get("nope")
    assert e.value.code == "UNKNOWN_REPOSITORY"
    assert "workspace" in e.value.context["known"]


def test_wrong_version():
    with pytest.raises(PinError) as e:
        Registry.from_dict({"version": 2, "repositories": {}})
    assert e.value.code == "UNSUPPORTED_VERSION"


def test_missing_fetch_urls():
    with pytest.raises(PinError):
        Registry.from_dict({"version": 1, "repositories": {"x": {}}})


def test_credential_url_rejected():
    with pytest.raises(PinError) as e:
        Registry.from_dict(base(fetch_urls=["https://user:pw@example.invalid/x.git"]))
    assert e.value.code == "INVALID_REGISTRY"


def test_unknown_repo_key_rejected():
    with pytest.raises(PinError):
        Registry.from_dict(base(surprise=1))


def test_bad_object_format():
    with pytest.raises(PinError):
        Registry.from_dict(base(object_format="md5"))


def test_ref_allowed_matching():
    reg = Registry.from_dict(base(allowed_ref_patterns=["refs/heads/main", "refs/heads/release/*"]))
    repo = reg.get("workspace")
    assert repo.ref_allowed("refs/heads/main")
    assert repo.ref_allowed("refs/heads/release/1.2")
    assert not repo.ref_allowed("refs/heads/feature/x")
    assert not repo.ref_allowed("refs/tags/v1")


def test_load_from_file(tmp_path):
    p = tmp_path / "registry.json"
    p.write_text(json.dumps(base()))
    reg = Registry.load(p)
    assert reg.source == p
    assert reg.get("workspace")


def test_load_missing_file(tmp_path):
    with pytest.raises(PinError) as e:
        Registry.load(tmp_path / "absent.json")
    assert e.value.code == "INVALID_REGISTRY"
