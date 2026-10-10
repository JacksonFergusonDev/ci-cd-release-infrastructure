#!/usr/bin/env python3
"""Synchronizes a Homebrew formula using a local uv manifest.

Extracts the sdist URL and SHA256 hash from a GitHub release tarball,
updates the Homebrew formula file, parses the caller's dependencies via
`uv export`, directly fetches PyPI sdist vectors, and splices
those resources into the formula using specified sentinels.
"""

import argparse
import hashlib
import re
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

try:
    from ._brew_utils import (
        get_pypi_sdist,
        log,
        log_group,
        resolve_and_validate_formula,
        run_cmd,
        run_logged,
        splice_formula,
        write_summary,
    )
except ImportError:
    from _brew_utils import (  # type: ignore[import-not-found,no-redef]
        get_pypi_sdist,
        log,
        log_group,
        resolve_and_validate_formula,
        run_cmd,
        run_logged,
        splice_formula,
        write_summary,
    )


def get_sha256(url: str) -> str:
    """Fetches a file over HTTP and returns its SHA256 checksum."""
    log(f"Fetching {url}...")
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            hasher = hashlib.sha256()
            for chunk in iter(lambda: response.read(4096), b""):
                hasher.update(chunk)
            return hasher.hexdigest()
    except urllib.error.URLError as e:
        sys.exit(f"Error fetching tarball: {e}")


def main() -> None:
    """Main entry point for the script."""
    parser = argparse.ArgumentParser(description="Sync Homebrew formula locally.")
    parser.add_argument(
        "--repo", required=True, help="GitHub repository (e.g., owner/repo)"
    )
    parser.add_argument("--tag", required=True, help="Release tag (e.g., v0.1.0)")
    parser.add_argument("--formula", type=Path, default=None, help="Path to formula")
    parser.add_argument(
        "--caller-dir", type=Path, required=True, help="Caller repo root"
    )
    parser.add_argument(
        "--tap-dir", type=Path, default=None, help="Homebrew tap directory"
    )
    parser.add_argument(
        "--package", default=None, help="Target package name (optional)"
    )
    args = parser.parse_args()

    with log_group("1/4 Validate formula configuration"):
        caller_dir: Path = args.caller_dir.resolve()
        if not caller_dir.exists():
            sys.exit(f"Caller directory not found: {caller_dir}")

        package_name, formula_path, relative_formula = resolve_and_validate_formula(
            caller_dir=caller_dir,
            tap_dir=args.tap_dir,
            formula_path=args.formula,
            package_name=args.package,
        )

    log(f"Package: {package_name} | Release: {args.tag} | Formula: {relative_formula}")

    with log_group("2/4 Fetch release tarball"):
        # 1. Resolve Root URL and Hash
        tarball_url = (
            f"https://github.com/{args.repo}/archive/refs/tags/{args.tag}.tar.gz"
        )
        new_sha = get_sha256(tarball_url)

    with log_group("3/4 Resolve Python resources"):
        # 2. Export strict local dependencies
        log(f"Exporting local dependencies from {caller_dir}...")

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            reqs_file = tmp_path / "reqs.txt"

            run_cmd(
                [
                    "uv",
                    "export",
                    "--no-dev",
                    "--no-hashes",
                    "--format",
                    "requirements-txt",
                    "-o",
                    str(reqs_file),
                ],
                cwd=caller_dir,
            )

            # 3. Parse requirements and query PyPI directly (Bypassing poet entirely)
            log("Resolving PyPI resource blocks...")
            resource_blocks: list[str] = []

            with open(reqs_file, encoding="utf-8") as f:
                for line in f:
                    # Strip environment markers (e.g., ; python_version >= '3.9')
                    line = line.split(";")[0].strip()

                    # Skip comments and flags
                    if not line or line.startswith("#") or line.startswith("-"):
                        continue

                    if "==" in line:
                        pkg, version = line.split("==")
                        pkg = re.sub(r"\[.*\]", "", pkg).strip()
                        version = version.strip()

                        # Excise the root package to pass Homebrew audits
                        if pkg.lower() == package_name.lower():
                            continue

                        log(
                            f"Resource {len(resource_blocks) + 1}: Fetching {pkg}=={version}"
                        )
                        sdist_url, sdist_sha = get_pypi_sdist(pkg, version)

                        block = (
                            f'  resource "{pkg}" do\n'
                            f'    url "{sdist_url}"\n'
                            f'    sha256 "{sdist_sha}"\n'
                            f"  end"
                        )
                        resource_blocks.append(block)

            resource_text = "\n\n".join(resource_blocks)

    with log_group("4/4 Update formula"):
        # 4. Splice File Content
        splice_formula(formula_path, tarball_url, new_sha, resource_text)

    log(
        f"Updated {relative_formula} for {package_name} {args.tag} "
        f"with {len(resource_blocks)} Python resources.",
        level="success",
    )
    write_summary(package_name, args.tag, relative_formula, len(resource_blocks))


if __name__ == "__main__":
    run_logged(main)
