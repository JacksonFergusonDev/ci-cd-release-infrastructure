# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "tomlkit>=0.15.1",
# ]
# ///

"""Standalone release orchestration script for uv-based Python projects.

Executes a two-phase release workflow:
1. Phase 1 (Pre-Flight): Read-only validations (clean working tree, branch guard,
   remote sync check, dry-run SemVer computation, local & remote tag collision checks).
2. Phase 2 (Transactional Execution): Atomically updates pyproject.toml, updates lockfile,
   creates release commit, creates annotated tag, and atomically pushes to remote,
   with automated rollback if any step fails or is interrupted.
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import TextIO

import tomlkit

SEMVER_RE = re.compile(
    r"^(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)


_OUTPUT_COLORS = {
    "phase": "1;36",
    "info": "36",
    "success": "32",
    "warning": "33",
    "error": "31",
}


def _styled(text: str, code: str, stream: TextIO) -> str:
    """Apply ANSI color only to a capable terminal, respecting NO_COLOR."""
    if (
        not stream.isatty()
        or "NO_COLOR" in os.environ
        or os.environ.get("TERM") == "dumb"
    ):
        return text
    return f"\033[{code}m{text}\033[0m"


def _output(
    message: str,
    *,
    prefix: str = "info",
    stderr: bool = False,
    blank: bool = False,
) -> None:
    """Print a consistent status prefix, with color based on its destination."""
    stream = sys.stderr if stderr else sys.stdout
    label = f"[{prefix}]"
    label = _styled(f"{label:<9}", _OUTPUT_COLORS.get(prefix, "36"), stream)
    message = message.replace("\n", "\n          ")
    print(f"{'\n' if blank else ''}{label} {message}", file=stream, flush=True)


def get_clean_env() -> dict[str, str]:
    """Return an environment stripped of uv's internal script virtualenv variables."""
    env = os.environ.copy()
    env.pop("VIRTUAL_ENV", None)
    env.pop("PYTHONHOME", None)
    return env


def run_cmd(
    cmd: list[str],
    *,
    capture: bool = True,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Execute a system command and return the completed process."""
    if env is None:
        env = get_clean_env()
    try:
        return subprocess.run(
            cmd,
            capture_output=capture,
            text=True,
            check=check,
            env=env,
        )
    except subprocess.CalledProcessError as e:
        if capture:
            if e.stdout:
                sys.stdout.write(e.stdout)
            if e.stderr:
                sys.stderr.write(e.stderr)
        raise


def atomic_write_text(path: Path, content: str, encoding: str = "utf-8") -> None:
    """Atomically writes text content to a file via a temporary file swap."""
    file_descriptor, temp_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    temp_path = Path(temp_name)

    try:
        with os.fdopen(file_descriptor, "w", encoding=encoding) as temp_file:
            temp_file.write(content)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_path, path)
    except Exception:
        with suppress(OSError):
            temp_path.unlink()
        raise


def compute_bumped_version(current_version: str, part: str) -> str:
    """Computes the incremented semantic version."""
    match = SEMVER_RE.match(str(current_version))
    if not match:
        raise ValueError(f"Current version '{current_version}' is not valid SemVer.")

    major = int(match["major"])
    minor = int(match["minor"])
    patch = int(match["patch"])

    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    if part == "patch":
        return f"{major}.{minor}.{patch + 1}"

    raise ValueError(f"Invalid version part: '{part}'. Choose major, minor, or patch.")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse release command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Safely cuts an atomic release for uv-based Python projects."
    )
    parser.add_argument(
        "part", choices=["major", "minor", "patch"], help="The version part to bump"
    )
    parser.add_argument(
        "--branch",
        default="main",
        help="The expected release branch (default: main).",
    )
    parser.add_argument(
        "--remote",
        default="origin",
        help="The git remote name (default: origin).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate pre-flight checks and preview the candidate version without making changes.",
    )
    parser.add_argument(
        "--no-push",
        action="store_true",
        help="Commit and tag locally without pushing to remote.",
    )
    parser.add_argument(
        "--pre-flight",
        action="append",
        default=[],
        dest="pre_flight_cmds",
        help="Optional shell command to execute during pre-flight checks (can be specified multiple times).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """Main release orchestration routine."""
    args = parse_args(argv)

    # --------------------------------------------------
    # Phase 1: Pre-Flight Checks (Strictly Read-Only)
    # --------------------------------------------------
    _output("Pre-flight checks", prefix="phase", blank=True)
    _output(f"Branch: {args.branch} | Remote: {args.remote}")
    _output("Checking tool prerequisites", prefix="1/6")
    for tool in ("git", "uv"):
        if not shutil.which(tool):
            _output(
                f"Required tool '{tool}' not found in PATH.",
                prefix="error",
                stderr=True,
            )
            sys.exit(1)

    _output("Verifying release branch", prefix="2/6")
    try:
        branch_res = run_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"])
        current_branch = branch_res.stdout.strip()
    except subprocess.CalledProcessError:
        _output("Failed to determine current git branch.", prefix="error", stderr=True)
        sys.exit(1)

    if current_branch != args.branch:
        _output(
            f"Releases must be cut from '{args.branch}' branch (currently on '{current_branch}').",
            prefix="error",
            stderr=True,
        )
        sys.exit(1)

    _output("Checking for uncommitted or untracked changes", prefix="3/6")
    status_res = run_cmd(["git", "status", "--porcelain"])
    if status_res.stdout.strip():
        _output(
            "Working directory is dirty. Please commit, stash, or clean all changes first:",
            prefix="error",
            stderr=True,
        )
        sys.stderr.write(status_res.stdout)
        sys.exit(1)

    _output("Checking synchronization with remote", prefix="4/6")
    try:
        run_cmd(["git", "fetch", args.remote, args.branch, "--tags", "--quiet"])
        local_hash = run_cmd(["git", "rev-parse", "HEAD"]).stdout.strip()
        remote_hash = run_cmd(
            ["git", "rev-parse", f"{args.remote}/{args.branch}"]
        ).stdout.strip()
    except subprocess.CalledProcessError as e:
        _output(
            f"Failed to check remote synchronization with '{args.remote}/{args.branch}': {e}",
            prefix="error",
            stderr=True,
        )
        sys.exit(1)

    if local_hash != remote_hash:
        _output(
            f"Local branch '{args.branch}' ({local_hash[:8]}) does not match "
            f"'{args.remote}/{args.branch}' ({remote_hash[:8]}).\n"
            "Please pull or push changes before releasing.",
            prefix="error",
            stderr=True,
        )
        sys.exit(1)

    _output("Validating pyproject.toml & SemVer", prefix="5/6")
    pyproject_path = Path("pyproject.toml")
    if not pyproject_path.exists():
        _output(
            "pyproject.toml not found in working directory.",
            prefix="error",
            stderr=True,
        )
        sys.exit(1)

    try:
        raw_text = pyproject_path.read_text(encoding="utf-8")
        doc = tomlkit.parse(raw_text)
        current_version = str(doc["project"]["version"])
        new_version = compute_bumped_version(current_version, args.part)
    except Exception as e:
        _output(
            f"Failed to read or calculate version from pyproject.toml: {e}",
            prefix="error",
            stderr=True,
        )
        sys.exit(1)

    new_tag = f"v{new_version}"
    _output(f"Current version: {current_version}")
    _output(f"Candidate release version: {new_version} (tag: {new_tag})")

    _output("Checking for tag collisions", prefix="6/6")
    local_tag_check = run_cmd(["git", "tag", "-l", new_tag]).stdout.strip()
    if local_tag_check:
        _output(f"Tag '{new_tag}' already exists locally.", prefix="error", stderr=True)
        sys.exit(1)

    remote_tag_check = run_cmd(
        ["git", "ls-remote", "--tags", args.remote, new_tag]
    ).stdout.strip()
    if remote_tag_check:
        _output(
            f"Tag '{new_tag}' already exists on remote '{args.remote}'.",
            prefix="error",
            stderr=True,
        )
        sys.exit(1)

    # Run optional custom pre-flight commands
    for cmd in args.pre_flight_cmds:
        _output(f"Running pre-flight command: {cmd}")
        try:
            subprocess.run(cmd, shell=True, check=True, env=get_clean_env())
        except subprocess.CalledProcessError as e:
            _output(
                f"Pre-flight command '{cmd}' failed with code {e.returncode}.",
                prefix="error",
                stderr=True,
            )
            sys.exit(1)

    if args.dry_run:
        _output(
            f"Pre-flight checks passed successfully. Candidate version is {new_version} (dry-run).",
            prefix="success",
            blank=True,
        )
        return

    # --------------------------------------------------
    # Phase 2: Transactional Execution & Rollback Guard
    # --------------------------------------------------
    _output("Pre-flight checks passed successfully.", prefix="success")
    _output(f"Executing release {new_tag}", prefix="phase", blank=True)
    _output(f"Version: {current_version} -> {new_version}")
    initial_rev = local_hash

    tag_created = False
    commit_created = False
    files_mutated = False

    def rollback() -> None:
        _output(
            "Release failed mid-flight! Rolling back local mutations...",
            prefix="warning",
            stderr=True,
            blank=True,
        )
        if tag_created:
            run_cmd(["git", "tag", "-d", new_tag], check=False)
        if commit_created:
            run_cmd(["git", "reset", "--hard", initial_rev], check=False)
        elif files_mutated:
            run_cmd(["git", "checkout", "--", "pyproject.toml", "uv.lock"], check=False)
        _output(
            f"Rollback complete. Repository cleanly restored to {initial_rev[:8]}.",
            prefix="success",
            stderr=True,
        )

    try:
        # 1. Mutate pyproject.toml
        _output(f"Updating pyproject.toml to version {new_version}", prefix="1/5")
        doc["project"]["version"] = new_version
        atomic_write_text(pyproject_path, tomlkit.dumps(doc))
        files_mutated = True

        # 2. Synchronize lockfile
        _output("Updating lockfile via uv sync", prefix="2/5")
        run_cmd(["uv", "sync"], capture=False)
        run_cmd(["uv", "lock", "--check"], capture=False)

        # 3. Stage and commit
        _output(f"Creating release commit for {new_version}", prefix="3/5")
        run_cmd(["git", "add", "pyproject.toml", "uv.lock"])
        run_cmd(["git", "commit", "-m", f"chore: bump version to {new_version}"])
        commit_created = True

        # 4. Create annotated tag
        _output(f"Creating annotated tag {new_tag}", prefix="4/5")
        run_cmd(["git", "tag", "-a", new_tag, "-m", f"Bump version to {new_tag}"])
        tag_created = True

        # 5. Push atomically to remote
        if not args.no_push:
            _output(
                f"Atomically pushing commit and {new_tag} to {args.remote}",
                prefix="5/5",
            )
            run_cmd(
                ["git", "push", args.remote, "HEAD", "--tags", "--atomic"],
                capture=False,
            )
        else:
            _output(
                f"Skipping push (--no-push). Local tag {new_tag} created.",
                prefix="5/5",
            )

        if args.no_push:
            _output(
                f"Release {new_tag} created locally; push skipped.",
                prefix="success",
                blank=True,
            )
        else:
            _output(f"Successfully released {new_tag}!", prefix="success", blank=True)

    except (KeyboardInterrupt, Exception) as e:
        rollback()
        if isinstance(e, KeyboardInterrupt):
            _output("Release aborted by user.", prefix="error", stderr=True)
            sys.exit(130)
        _output(f"Release failed: {e}", prefix="error", stderr=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
