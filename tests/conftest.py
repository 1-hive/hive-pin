# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared fixtures: throwaway git repositories with a bare 'published' remote."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from hivepin.config import Config
from hivepin.core import mint
from hivepin.registry import Registry

_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
    "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",
}


def git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "-c", "init.defaultBranch=main",
         "-C", str(cwd), *args],
        capture_output=True, text=True, check=check, env=_GIT_ENV,
    )


@dataclass
class Scenario:
    tmp: Path
    work: Path            # a working clone (registry local_path)
    remote: Path          # bare "published" remote
    repo_id: str = "workspace"
    object_format: str = "sha1"

    def commit_all(self, message: str) -> str:
        git(self.work, "add", "-A")
        git(self.work, "commit", "-q", "-m", message)
        return git(self.work, "rev-parse", "HEAD").stdout.strip()

    def push(self, ref: str = "main") -> None:
        git(self.work, "push", "-q", "origin", ref)

    def write(self, rel: str, content: str | bytes, *, mode: int | None = None) -> Path:
        p = self.work / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            p.write_bytes(content)
        else:
            p.write_text(content)
        if mode is not None:
            p.chmod(mode)
        return p

    def registry_dict(self, **repo_overrides) -> dict:
        repo = {
            "fetch_urls": [self.remote.as_uri()],
            "object_format": self.object_format,
            "allowed_ref_patterns": ["refs/heads/main"],
            "local_path": str(self.work),
        }
        repo.update(repo_overrides)
        return {"version": 1, "repositories": {self.repo_id: repo}}

    def registry(self, **repo_overrides) -> Registry:
        return Registry.from_dict(self.registry_dict(**repo_overrides))

    def config(self, **overrides) -> Config:
        overrides.setdefault("cache_directory", str(self.tmp / "cache"))
        return Config.load(**overrides)


def _make_scenario(tmp: Path, object_format: str = "sha1") -> Scenario:
    remote = tmp / "remote.git"
    subprocess.run(["git", "init", "--bare", "-b", "main",
                    f"--object-format={object_format}", str(remote)],
                   check=True, capture_output=True, env=_GIT_ENV)
    work = tmp / "work"
    subprocess.run(["git", "init", "-b", "main", f"--object-format={object_format}", str(work)],
                   check=True, capture_output=True, env=_GIT_ENV)
    git(work, "remote", "add", "origin", str(remote))
    sc = Scenario(tmp=tmp, work=work, remote=remote, object_format=object_format)
    sc.write("reports/result.md", "hello world\n")
    sc.write("bin/run.sh", "#!/bin/sh\necho hi\n", mode=0o755)
    sc.write("docs/nested/deep.txt", "deep\n")
    sc.write("data/bytes.bin", bytes(range(256)))
    sc.commit_all("seed")
    sc.push()
    return sc


@pytest.fixture
def scenario(tmp_path: Path) -> Scenario:
    return _make_scenario(tmp_path / "s")


@pytest.fixture
def sha256_scenario(tmp_path: Path) -> Scenario:
    return _make_scenario(tmp_path / "s256", object_format="sha256")


def v1_mint(*args, **kwargs):
    """The v1 suite mints v1 pins (v2 is the default)."""
    return mint(*args, version=1, **kwargs)
