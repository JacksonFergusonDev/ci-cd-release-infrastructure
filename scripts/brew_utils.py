import json
import os
import re
import subprocess
import sys
import tomllib
import urllib.error
import urllib.request
from pathlib import Path


def get_caller_project_name(caller_dir: Path) -> str | None:
    """Extracts project name from pyproject.toml in the caller directory if available."""
    pyproject_path = caller_dir / "pyproject.toml"
    if not pyproject_path.exists():
        return None

    try:
        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)
            name = data.get("project", {}).get("name")
            if name:
                return str(name)
            poetry_name = data.get("tool", {}).get("poetry", {}).get("name")
            if poetry_name:
                return str(poetry_name)
            flit_name = (
                data.get("tool", {}).get("flit", {}).get("metadata", {}).get("module")
            )
            if flit_name:
                return str(flit_name)
    except Exception as e:
        print(f"Warning: Failed to parse {pyproject_path}: {e}", file=sys.stderr)
    return None


def resolve_and_validate_formula(
    caller_dir: Path | None = None,
    tap_dir: Path | None = None,
    formula_path: Path | None = None,
    package_name: str | None = None,
) -> tuple[str, Path, str]:
    """Resolves and validates target package name and formula file.

    Args:
        caller_dir: Path to the caller repository root, if available.
        tap_dir: Path to the Homebrew tap directory, if available.
        formula_path: Explicit path to the Ruby formula file, if provided.
        package_name: Explicit target PyPI package name, if provided.

    Returns:
        A tuple of (resolved_package_name, resolved_formula_path, relative_formula_path).
    """
    caller_project_name = (
        get_caller_project_name(caller_dir)
        if caller_dir and caller_dir.exists()
        else None
    )

    # 1. Resolve package name
    resolved_package: str | None = None
    if package_name:
        resolved_package = package_name.strip()
        if (
            caller_project_name
            and resolved_package.lower() != caller_project_name.lower()
        ):
            sys.exit(
                f"\n❌ [Configuration Error] Package name mismatch!\n"
                f"   Caller repository defines project: '{caller_project_name}'\n"
                f"   Workflow input 'package_name' was:  '{resolved_package}'\n\n"
                f"Possible causes:\n"
                f"1. An explicit 'package_name' was configured in the workflow call (e.g., from an older\n"
                f"   workflow template). You can remove 'package_name' from the workflow 'with:' block to\n"
                f"   automatically infer '{caller_project_name}'.\n"
                f"2. The 'package_name' input has a typo.\n"
                f"3. If '{resolved_package}' is intentionally different from pyproject.toml, verify your\n"
                f"   workflow configuration.\n"
            )
    elif caller_project_name:
        resolved_package = caller_project_name
    elif formula_path:
        resolved_package = formula_path.stem

    if not resolved_package:
        sys.exit(
            "\n❌ [Configuration Error] Could not determine package name!\n"
            "No 'pyproject.toml' with a project name was found in the caller directory,\n"
            "and no 'package_name' or 'formula_path' was provided. Please provide 'package_name'\n"
            "in the workflow inputs.\n"
        )

    # 2. Resolve formula path
    if formula_path:
        formula_stem = formula_path.stem
        expected_stem = caller_project_name or resolved_package
        if formula_stem.lower() != expected_stem.lower():
            sys.exit(
                f"\n❌ [Configuration Error] Formula path mismatch!\n"
                f"   Formula path stem is:   '{formula_stem}' ({formula_path})\n"
                f"   Expected project/package: '{expected_stem}'\n\n"
                f"Possible causes:\n"
                f"1. An explicit 'formula_path' was configured in the workflow call (e.g., from an older\n"
                f"   workflow template). You can remove 'formula_path' from the workflow 'with:' block to\n"
                f"   default to 'Formula/{expected_stem}.rb'.\n"
                f"2. The formula path input has a typo or points to the wrong formula.\n"
            )

        # Check if formula_path is relative to tap_dir or standalone
        if not formula_path.exists() and tap_dir and (tap_dir / formula_path).exists():
            resolved_formula = (tap_dir / formula_path).resolve()
        else:
            resolved_formula = formula_path.resolve()

        if tap_dir and resolved_formula.is_relative_to(tap_dir.resolve()):
            rel_formula_path = str(resolved_formula.relative_to(tap_dir.resolve()))
        else:
            rel_formula_path = str(formula_path)
    else:
        rel_formula_path = f"Formula/{resolved_package}.rb"
        if tap_dir:
            resolved_formula = (tap_dir / rel_formula_path).resolve()
        else:
            resolved_formula = Path(rel_formula_path).resolve()

    # 3. Validate formula existence
    if not resolved_formula.exists():
        sys.exit(
            f"\n❌ [Formula Error] Formula not found: {resolved_formula}\n\n"
            f"Possible causes:\n"
            f"1. The formula for '{resolved_package}' has not been added to JacksonFergusonDev/homebrew-tap yet.\n"
            f"   Please create '{rel_formula_path}' in the tap repository.\n"
            f"2. The formula is located in a different path. If so, specify 'formula_path' in the workflow inputs.\n"
        )

    # 4. Export to GITHUB_OUTPUT if available
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        try:
            with open(github_output, "a", encoding="utf-8") as f:
                f.write(f"package_name={resolved_package}\n")
                f.write(f"formula_path={rel_formula_path}\n")
        except Exception as e:
            print(f"Warning: Failed to write to GITHUB_OUTPUT: {e}", file=sys.stderr)

    return resolved_package, resolved_formula, rel_formula_path


def get_pypi_sdist(package: str, version: str) -> tuple[str, str]:
    """Queries PyPI for the sdist URL and SHA256 of a specific dependency."""
    url = f"https://pypi.org/pypi/{package}/{version}/json"
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as e:
        sys.exit(f"Failed to fetch PyPI metadata for {package}=={version}: {e}")

    for info in data.get("urls", []):
        if info.get("packagetype") == "sdist":
            return str(info["url"]), str(info["digests"]["sha256"])

    sys.exit(f"No sdist found for {package}=={version} on PyPI.")


def run_cmd(args: list[str], cwd: Path | None = None) -> str:
    """Executes a shell command and returns its standard output."""
    try:
        res = subprocess.run(args, capture_output=True, text=True, check=True, cwd=cwd)
        return res.stdout
    except subprocess.CalledProcessError as e:
        print(f"Command failed: {' '.join(args)}", file=sys.stderr)
        print(f"Stdout: {e.stdout}", file=sys.stderr)
        print(f"Stderr: {e.stderr}", file=sys.stderr)
        raise


def splice_formula(
    formula_path: Path, new_url: str, new_sha: str, resource_text: str
) -> None:
    """Splice File Content for a Homebrew formula."""
    content = formula_path.read_text(encoding="utf-8")

    content = re.sub(
        r'^  url\s+".*"', f'  url "{new_url}"', content, flags=re.MULTILINE, count=1
    )
    content = re.sub(
        r'^  sha256\s+".*"',
        f'  sha256 "{new_sha}"',
        content,
        flags=re.MULTILINE,
        count=1,
    )

    pattern = r"(?<=# RESOURCE_BLOCK_START\n).*?(?=# RESOURCE_BLOCK_END)"
    replacement = f"{resource_text}\n  " if resource_text else "  "
    content = re.sub(pattern, replacement, content, flags=re.DOTALL)

    formula_path.write_text(content, encoding="utf-8")
