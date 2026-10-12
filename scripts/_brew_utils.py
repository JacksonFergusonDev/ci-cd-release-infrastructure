import json
import os
import re
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from html import escape
from pathlib import Path
from typing import Any, Literal


def _github_actions() -> bool:
    return os.environ.get("GITHUB_ACTIONS") == "true"


def _escape_command(message: str) -> str:
    """Escape workflow command data so messages cannot introduce commands."""
    return message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def log(
    message: str,
    *,
    level: Literal["info", "success", "warning", "error"] = "info",
) -> None:
    """Emit readable status messages or GitHub warning/error annotations."""
    if _github_actions() and level in {"warning", "error"}:
        print(f"::{level}::{_escape_command(message)}", flush=True)
        return
    stream = sys.stderr if level in {"warning", "error"} else sys.stdout
    label = f"[{level}]"
    message = message.replace("\n", "\n          ")
    print(f"{label:<9} {message}", file=stream, flush=True)


@contextmanager
def log_group(title: str) -> Iterator[None]:
    """Group runner logs and always close the group, including on failure."""
    github = _github_actions()
    if github:
        print(f"::group::{_escape_command(title)}", flush=True)
    else:
        print(f"\n[phase]   {title}", flush=True)
    try:
        yield
    finally:
        if github:
            print("::endgroup::", flush=True)


def write_summary(
    package: str, version: str, formula: str, resource_count: int
) -> None:
    """Append a formula update summary without implying audit or push success."""
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not _github_actions() or not summary_path:
        return

    def cell(value: str) -> str:
        return (
            escape(value).replace("|", "&#124;").replace("\r", " ").replace("\n", " ")
        )

    summary = (
        "### Homebrew formula updated\n\n"
        "| Field | Value |\n| --- | --- |\n"
        f"| Package | <code>{cell(package)}</code> |\n"
        f"| Release | <code>{cell(version)}</code> |\n"
        f"| Formula | <code>{cell(formula)}</code> |\n"
        f"| Python resources | {resource_count} |\n\n"
        "Formula generation completed. Audit, commit, and push run in a later step.\n\n"
    )
    try:
        with open(summary_path, "a", encoding="utf-8") as summary_file:
            summary_file.write(summary)
    except OSError as e:
        log(f"Could not write job summary: {e}", level="warning")


def run_logged(main: Callable[[], None]) -> None:
    """Report expected CLI failures as annotations while retaining exit codes."""
    try:
        main()
    except SystemExit as e:
        if isinstance(e.code, str):
            log(e.code.strip(), level="error")
            raise SystemExit(1) from None
        if e.code:
            log(f"Formula update exited with status {e.code}.", level="error")
        raise
    except KeyboardInterrupt:
        log("Formula update interrupted.", level="error")
        raise SystemExit(130) from None
    except Exception as e:
        log(f"Formula update failed: {e}", level="error")
        raise SystemExit(1) from None


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
        log(f"Failed to parse {pyproject_path}: {e}", level="warning")
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
    formula_name = resolved_formula.stem
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        try:
            with open(github_output, "a", encoding="utf-8") as f:
                f.write(f"package_name={resolved_package}\n")
                f.write(f"formula_name={formula_name}\n")
                f.write(f"formula_path={rel_formula_path}\n")
        except Exception as e:
            log(f"Failed to write to GITHUB_OUTPUT: {e}", level="warning")

    return resolved_package, resolved_formula, rel_formula_path


def get_pypi_sdist(package: str, version: str) -> tuple[str, str]:
    """Queries PyPI for the sdist URL and SHA256 of a specific dependency."""
    url = f"https://pypi.org/pypi/{package}/{version}/json"
    req = urllib.request.Request(url)
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                data = json.loads(response.read().decode("utf-8"))
            break
        except (urllib.error.URLError, TimeoutError) as e:
            permanent = isinstance(e, urllib.error.HTTPError) and (
                e.code != 429 and e.code < 500
            )
            if permanent or attempt == 3:
                sys.exit(f"Failed to fetch PyPI metadata for {package}=={version}: {e}")
            delay = 2**attempt
            log(
                f"PyPI request for {package}=={version} failed: {e}. "
                f"Retrying in {delay}s (attempt {attempt + 2}/4)."
            )
            time.sleep(delay)

    sdist = find_sdist(data)
    if sdist is None:
        sys.exit(f"No sdist found for {package}=={version} on PyPI.")
    return sdist


def find_sdist(metadata: dict[str, Any]) -> tuple[str, str] | None:
    """Return the sdist URL and SHA256 from a PyPI JSON payload, if present."""
    for info in metadata.get("urls", []):
        if info.get("packagetype") == "sdist":
            return str(info["url"]), str(info["digests"]["sha256"])
    return None


def canonical_name(name: str) -> str:
    """Normalize a distribution name per PEP 503 (e.g. ``My_Pkg`` -> ``my-pkg``)."""
    return re.sub(r"[-_.]+", "-", name).lower()


# Homebrew's primary platform. Resources are sdists, so the target only decides
# which environment markers apply (e.g. dropping Windows- or Linux-only deps).
TARGET_PLATFORM = "aarch64-apple-darwin"


def formula_python_version(formula_path: Path) -> str | None:
    """Return X.Y from the formula's ``depends_on "python@X.Y"``, if declared."""
    content = formula_path.read_text(encoding="utf-8")
    match = re.search(r'^\s*depends_on\s+"python@(\d+\.\d+)"', content, re.MULTILINE)
    return match[1] if match else None


def target_resolution_args(formula_path: Path) -> list[str]:
    """Build ``uv pip compile`` flags that resolve for the formula's install target."""
    args = ["--python-platform", TARGET_PLATFORM]
    python_version = formula_python_version(formula_path)
    if python_version:
        args += ["--python-version", python_version]
    else:
        log(
            f'No depends_on "python@X.Y" found in {formula_path.name}; '
            "resolving for the runner's Python version.",
            level="warning",
        )
    log(
        f"Resolving for {TARGET_PLATFORM}, "
        f"Python {python_version or 'from the runner'}."
    )
    return args


def locked_requirements(caller_dir: Path, formula_path: Path, out_dir: Path) -> Path:
    """Export the caller's locked runtime dependencies for the formula's target.

    The formula ships exactly the versions the caller's CI tested, so a stale
    lockfile fails the run instead of being re-resolved.

    Args:
        caller_dir: Root of the caller repository, containing ``uv.lock``.
        formula_path: The formula, whose Python version sets the target.
        out_dir: Directory for the intermediate requirements files.

    Returns:
        Path to pinned requirements with markers evaluated for the target.
    """
    if not (caller_dir / "uv.lock").is_file():
        sys.exit(
            f"No uv.lock found in {caller_dir}. The formula's Python resources "
            "come from the caller's lockfile; commit one with `uv lock`."
        )

    exported = out_dir / "exported.txt"
    requirements = out_dir / "reqs.txt"
    run_cmd(
        [
            "uv",
            "export",
            "--locked",
            "--no-dev",
            "--no-emit-project",
            "--no-hashes",
            "--format",
            "requirements-txt",
            "-o",
            str(exported),
        ],
        cwd=caller_dir,
    )

    # The export is universal (one pin per marker fork). Keep the locked
    # versions but evaluate markers for the formula's install target.
    run_cmd(
        [
            "uv",
            "pip",
            "compile",
            "--no-deps",
            "--no-annotate",
            "--no-header",
            *target_resolution_args(formula_path),
            str(exported),
            "-o",
            str(requirements),
        ]
    )
    return requirements


def build_resource_blocks(requirements: Path, root_package: str) -> list[str]:
    """Render Homebrew resource blocks for pinned requirements using PyPI sdists.

    Requirements must already be resolved for the target platform. The root
    package is excluded so the formula passes `brew audit`.
    """
    resource_blocks: list[str] = []
    seen: dict[str, str] = {}
    root = canonical_name(root_package)

    with open(requirements, encoding="utf-8") as f:
        for line in f:
            # Strip environment markers (e.g., ; python_version >= '3.9')
            line = line.split(";")[0].strip()

            # Skip comments and flags
            if not line or line.startswith("#") or line.startswith("-"):
                continue

            if "==" not in line or " @ " in line:
                log(
                    f"Skipping '{line}': only exact PyPI pins (name==version) "
                    "can become Homebrew resources.",
                    level="warning",
                )
                continue

            pkg, version = line.split("==")
            pkg = re.sub(r"\[.*\]", "", pkg).strip()
            version = version.strip()
            name = canonical_name(pkg)

            # Excise the root package to pass Homebrew audits
            if name == root:
                continue

            if name in seen:
                if seen[name] == version:
                    continue
                sys.exit(
                    f"Conflicting pins for {pkg}: {seen[name]} and {version}. "
                    "Requirements must be resolved for a single target platform."
                )
            seen[name] = version

            log(f"Resource {len(resource_blocks) + 1}: Fetching {pkg}=={version}")
            sdist_url, sdist_sha = get_pypi_sdist(pkg, version)

            block = (
                f'  resource "{pkg}" do\n'
                f'    url "{sdist_url}"\n'
                f'    sha256 "{sdist_sha}"\n'
                f"  end"
            )
            resource_blocks.append(block)

    return resource_blocks


def run_cmd(args: list[str], cwd: Path | None = None) -> str:
    """Executes a shell command and returns its standard output."""
    try:
        res = subprocess.run(args, capture_output=True, text=True, check=True, cwd=cwd)
        return res.stdout
    except subprocess.CalledProcessError as e:
        print(f"Command failed: {' '.join(args)}", file=sys.stderr, flush=True)
        print(f"Stdout: {e.stdout}", file=sys.stderr, flush=True)
        print(f"Stderr: {e.stderr}", file=sys.stderr, flush=True)
        raise


def splice_formula(
    formula_path: Path, new_url: str, new_sha: str, resource_text: str
) -> None:
    """Splice File Content for a Homebrew formula.

    Exits if any target is missing, rather than reporting a no-op as an update.
    """
    content = formula_path.read_text(encoding="utf-8")

    # Callables keep backslashes in the new values from being read as escapes.
    content, url_count = re.subn(
        r'^  url\s+".*"',
        lambda _: f'  url "{new_url}"',
        content,
        flags=re.MULTILINE,
        count=1,
    )
    content, sha_count = re.subn(
        r'^  sha256\s+".*"',
        lambda _: f'  sha256 "{new_sha}"',
        content,
        flags=re.MULTILINE,
        count=1,
    )

    pattern = r"(?<=# RESOURCE_BLOCK_START\n).*?(?=# RESOURCE_BLOCK_END)"
    replacement = f"{resource_text}\n  " if resource_text else "  "
    content, block_count = re.subn(
        pattern, lambda _: replacement, content, flags=re.DOTALL
    )

    missing = [
        target
        for target, count in [
            ('top-level `  url "..."`', url_count),
            ('top-level `  sha256 "..."`', sha_count),
            (
                "`# RESOURCE_BLOCK_START` / `# RESOURCE_BLOCK_END` sentinels",
                block_count,
            ),
        ]
        if count == 0
    ]
    if missing:
        sys.exit(
            f"Could not update {formula_path}; missing " + ", ".join(missing) + "."
        )

    formula_path.write_text(content, encoding="utf-8")
