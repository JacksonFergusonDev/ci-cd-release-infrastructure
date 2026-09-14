#!/usr/bin/env python3
"""Synchronizes a Homebrew formula with a newly published PyPI release.

This script polls PyPI for visibility of a specific version, extracts the
root sdist URL and SHA256 hash, dynamically resolves the dependency tree
using `uv pip compile`, fetches sdist vectors for all dependencies directly
from PyPI, and splices the formula using specified sentinels.
"""

import argparse
import json
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

try:
    from .brew_utils import (
        get_pypi_sdist,
        resolve_and_validate_formula,
        run_cmd,
        splice_formula,
    )
except ImportError:
    from brew_utils import (  # type: ignore[import-not-found,no-redef]
        get_pypi_sdist,
        resolve_and_validate_formula,
        run_cmd,
        splice_formula,
    )


def get_pypi_metadata(
    package_name: str, version: str, max_retries: int = 60, delay: int = 2
) -> dict[str, Any]:
    """Poll PyPI until the specified package version metadata becomes available."""
    url = f"https://pypi.org/pypi/{package_name}/{version}/json"
    print(f"Polling {url} for release visibility...")

    for _ in range(max_retries):
        try:
            with urllib.request.urlopen(url, timeout=15) as response:
                if response.status == 200:
                    data = response.read().decode("utf-8")
                    return json.loads(data)  # type: ignore[no-any-return]
        except urllib.error.HTTPError as e:
            if e.code != 404:
                print(f"HTTP Error querying PyPI: {e.code}", file=sys.stderr)

        time.sleep(delay)

    raise TimeoutError(f"Timed out waiting for {package_name} {version} on PyPI.")


def extract_sdist_info(metadata: dict[str, Any]) -> tuple[str, str]:
    """Parse the PyPI metadata payload for the source distribution details."""
    for url_info in metadata.get("urls", []):
        if url_info.get("packagetype") == "sdist":
            return str(url_info["url"]), str(url_info["digests"]["sha256"])

    raise ValueError("sdist information not found in PyPI metadata.")


def main() -> None:
    """Execute the Homebrew formula synchronization pipeline."""
    parser = argparse.ArgumentParser(
        description="Update Homebrew formula with newly published PyPI releases."
    )
    parser.add_argument("--version", required=True, help="Version tag (e.g., 0.7.0).")
    parser.add_argument(
        "--formula-path",
        type=Path,
        default=None,
        help="Path to the Ruby formula file.",
    )
    parser.add_argument(
        "--package",
        default=None,
        help="Target PyPI package name (optional, inferred from caller pyproject.toml).",
    )
    parser.add_argument(
        "--caller-dir",
        type=Path,
        default=None,
        help="Path to caller repository root.",
    )
    parser.add_argument(
        "--tap-dir",
        type=Path,
        default=None,
        help="Path to Homebrew tap repository root.",
    )
    args = parser.parse_args()

    # Strip 'v' prefix if present to ensure PyPI API compatibility
    args.version = args.version.lstrip("v")

    package_name, formula_path, _ = resolve_and_validate_formula(
        caller_dir=args.caller_dir,
        tap_dir=args.tap_dir,
        formula_path=args.formula_path,
        package_name=args.package,
    )

    # 1. Wait for registry sync
    metadata = get_pypi_metadata(package_name, args.version)

    # 2. Extract root distribution vectors
    new_url, new_sha = extract_sdist_info(metadata)
    print(f"Resolved root sdist:\n  URL: {new_url}\n  SHA: {new_sha}")

    # 3. Resolve the dependency tree via uv pip compile
    print("Resolving dependency tree...")
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        reqs_in = tmp_path / "reqs.in"
        reqs_txt = tmp_path / "reqs.txt"
        reqs_in.write_text(f"{package_name}=={args.version}", encoding="utf-8")

        run_cmd(
            [
                "uv",
                "pip",
                "compile",
                "--no-annotate",
                "--no-header",
                str(reqs_in),
                "-o",
                str(reqs_txt),
            ]
        )

        # 4. Parse requirements and query PyPI directly
        print("Resolving PyPI resource blocks...")
        resource_blocks = []

        with open(reqs_txt, encoding="utf-8") as f:
            for line in f:
                # Strip environment markers (e.g., ; python_version >= '3.9')
                line = line.split(";")[0].strip()

                if not line or line.startswith("#") or line.startswith("-"):
                    continue

                if "==" in line:
                    pkg, version = line.split("==")
                    pkg = re.sub(r"\[.*\]", "", pkg).strip()
                    version = version.strip()

                    # Excise the root package to pass Homebrew audits
                    if pkg.lower() == package_name.lower():
                        continue

                    print(f"  -> Fetching {pkg}=={version}")
                    sdist_url, sdist_sha = get_pypi_sdist(pkg, version)

                    block = (
                        f'  resource "{pkg}" do\n'
                        f'    url "{sdist_url}"\n'
                        f'    sha256 "{sdist_sha}"\n'
                        f"  end"
                    )
                    resource_blocks.append(block)

        resource_text = "\n\n".join(resource_blocks)

    # 5. Splice File Content
    splice_formula(formula_path, new_url, new_sha, resource_text)

    print("Successfully synchronized formula.")


if __name__ == "__main__":
    main()
