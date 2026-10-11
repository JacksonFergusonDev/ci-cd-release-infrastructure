"""Exercise release.py against real git repositories and a real uv.

The mocked tests in test_release.py check control flow; these check what
actually ends up in the local and remote repositories.
"""

import shutil
import subprocess
from pathlib import Path

import pytest
import tomlkit

from scripts import release

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or shutil.which("uv") is None,
    reason="requires git and uv",
)


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A clean clone on main, in sync with a bare origin, with no uv.lock yet."""
    # Isolate from the developer's git config (signing, hooks paths, etc.).
    gitconfig = tmp_path / "gitconfig"
    gitconfig.write_text(
        "[user]\n\tname = Test\n\temail = test@example.com\n"
        "[init]\n\tdefaultBranch = main\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("UV_OFFLINE", "1")
    monkeypatch.setenv("NO_COLOR", "1")

    origin = tmp_path / "origin.git"
    work = tmp_path / "work"
    git("init", "--bare", "-q", str(origin), cwd=tmp_path)
    git("clone", "-q", str(origin), str(work), cwd=tmp_path)

    (work / "pyproject.toml").write_text(
        '[project]\nname = "demo-pkg"\nversion = "1.0.0"\nrequires-python = ">=3.9"\n',
        encoding="utf-8",
    )
    git("add", ".", cwd=work)
    git("commit", "-q", "-m", "init", cwd=work)
    git("push", "-q", "origin", "main", cwd=work)

    monkeypatch.chdir(work)
    return work


def commit_lockfile(work: Path) -> None:
    subprocess.run(["uv", "lock", "-q"], cwd=work, check=True)
    git("add", "uv.lock", cwd=work)
    git("commit", "-q", "-m", "lock", cwd=work)
    git("push", "-q", "origin", "main", cwd=work)


def version_of(work: Path) -> str:
    doc = tomlkit.parse((work / "pyproject.toml").read_text(encoding="utf-8"))
    return str(doc["project"]["version"])


def assert_restored(work: Path, initial: str) -> None:
    assert git("rev-parse", "HEAD", cwd=work) == initial
    assert git("status", "--porcelain", "--ignored", cwd=work) == ""
    assert git("tag", "-l", "v1.1.0", cwd=work) == ""
    assert version_of(work) == "1.0.0"


def test_release_pushes_only_branch_and_new_tag(repo: Path) -> None:
    commit_lockfile(repo)
    git("tag", "stray-local-tag", cwd=repo)

    release.main(["minor"])

    origin = repo.parent / "origin.git"
    remote_tags = git("tag", "-l", cwd=origin).splitlines()
    assert remote_tags == ["v1.1.0"]
    assert git("rev-parse", "main", cwd=origin) == git("rev-parse", "HEAD", cwd=repo)
    assert version_of(repo) == "1.1.0"
    assert git("show", "--name-only", "--format=", "HEAD", cwd=repo).splitlines() == [
        "pyproject.toml",
        "uv.lock",
    ]


def test_rejected_push_rolls_back_commit_and_tag(repo: Path, capsys) -> None:
    commit_lockfile(repo)
    origin = repo.parent / "origin.git"
    hook = origin / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\necho rejected >&2\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    initial = git("rev-parse", "HEAD", cwd=repo)

    with pytest.raises(SystemExit) as exc:
        release.main(["minor"])

    assert exc.value.code == 1
    assert_restored(repo, initial)
    assert git("tag", "-l", cwd=origin) == ""
    assert "Rollback complete" in capsys.readouterr().err


def test_rollback_removes_lockfile_created_by_release(repo: Path, capsys) -> None:
    # No committed uv.lock: the release creates one. Fail at commit time.
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    initial = git("rev-parse", "HEAD", cwd=repo)

    with pytest.raises(SystemExit):
        release.main(["minor", "--no-push"])

    assert not (repo / "uv.lock").exists()
    assert_restored(repo, initial)
    assert "Rollback complete" in capsys.readouterr().err


def test_gitignored_lockfile_is_not_committed(repo: Path) -> None:
    (repo / ".gitignore").write_text("uv.lock\n", encoding="utf-8")
    git("add", ".gitignore", cwd=repo)
    git("commit", "-q", "-m", "ignore lock", cwd=repo)
    git("push", "-q", "origin", "main", cwd=repo)

    release.main(["patch", "--no-push"])

    assert version_of(repo) == "1.0.1"
    assert git("show", "--name-only", "--format=", "HEAD", cwd=repo) == (
        "pyproject.toml"
    )
    assert git("tag", "-l", "v1.0.1", cwd=repo) == "v1.0.1"
