# /// script
# requires-python = ">=3.13"
# ///

"""Render or update a prebuilt-binary Homebrew formula from release artifacts.

Supports two modes:
1. Render mode (when ``--desc`` is given): generates a complete, brew-style-clean
   prebuilt-binary Ruby formula for macOS-only or macOS+Linux architectures, with
   optional ``head do`` source builds via Cargo.
2. Splice mode (when ``--desc`` is omitted): updates ``version`` and each target's
   ``url`` and ``sha256`` in place inside an existing tap formula, preserving custom
   ``head``/``install``/``test`` blocks (e.g. bundled sidecars).
"""

import argparse
import hashlib
import os
import re
import sys
from html import escape
from pathlib import Path

from scripts._brew_utils import log, log_group, run_logged

SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")

SUPPORTED_TARGETS: dict[str, tuple[str, str]] = {
    "aarch64-apple-darwin": ("macos", "arm"),
    "x86_64-apple-darwin": ("macos", "intel"),
    "aarch64-unknown-linux-gnu": ("linux", "arm"),
    "x86_64-unknown-linux-gnu": ("linux", "intel"),
}


def formula_class_name(formula_stem: str) -> str:
    """Convert a kebab- or snake-case formula name into a Ruby class name."""
    parts = [part for part in re.split(r"[-_]+", formula_stem) if part]
    if not parts:
        raise ValueError(f"Invalid formula name: '{formula_stem}'.")
    return "".join(part[0].upper() + part[1:] for part in parts)


def parse_targets(raw: str) -> list[str]:
    """Parse and validate a comma-separated list of Rust/Homebrew target triples."""
    targets = [item.strip() for item in raw.split(",") if item.strip()]
    if not targets:
        sys.exit("At least one target triple must be provided via --targets.")
    unknown = [target for target in targets if target not in SUPPORTED_TARGETS]
    if unknown:
        supported = ", ".join(sorted(SUPPORTED_TARGETS))
        sys.exit(
            f"Unsupported target(s): {', '.join(unknown)}. Supported: {supported}."
        )
    if len(set(targets)) != len(targets):
        sys.exit(f"Duplicate target triple in --targets: '{raw}'.")
    return targets


def archive_filename(template: str, version: str, target: str) -> str:
    """Expand an archive filename template with version and target placeholders."""
    try:
        return template.format(version=version, target=target)
    except KeyError as e:
        sys.exit(
            f"Unknown placeholder {e} in --archive-template '{template}'. "
            "Supported placeholders: {version}, {target}."
        )


def release_url(repository: str, tag: str, archive: str) -> str:
    """Build the GitHub Releases download URL for a packaged archive."""
    return f"https://github.com/{repository}/releases/download/{tag}/{archive}"


def resolve_sha256(artifacts_dir: Path, archive: str) -> str:
    """Compute or read the SHA256 checksum for a release archive.

    When both the archive file and a sibling ``.sha256`` file exist, computes the
    digest from the archive and verifies it matches the checksum file.
    """
    archive_path = artifacts_dir / archive
    sha_path = artifacts_dir / f"{archive}.sha256"

    file_digest: str | None = None
    if archive_path.is_file():
        file_digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()

    recorded_digest: str | None = None
    if sha_path.is_file():
        tokens = sha_path.read_text(encoding="utf-8").strip().split()
        if not tokens or not SHA256_RE.match(tokens[0]):
            sys.exit(f"Invalid SHA256 checksum format in {sha_path}.")
        recorded_digest = tokens[0].lower()

    if file_digest is not None and recorded_digest is not None:
        if file_digest != recorded_digest:
            sys.exit(
                f"SHA256 mismatch for {archive}: archive digest {file_digest} "
                f"!= checksum file {recorded_digest}."
            )
        return file_digest
    if file_digest is not None:
        return file_digest
    if recorded_digest is not None:
        return recorded_digest

    sys.exit(
        f"Missing release artifact '{archive}' and checksum '{archive}.sha256' "
        f"in {artifacts_dir}."
    )


def _render_os_block(
    os_name: str,
    entries: dict[str, tuple[str, str]],
) -> list[str]:
    """Render an ``on_macos`` or ``on_linux`` block with ``on_arm`` / ``on_intel``."""
    lines = [f"  on_{os_name} do"]
    for arch in ("arm", "intel"):
        if arch in entries:
            url, sha = entries[arch]
            lines.extend(
                [
                    f"    on_{arch} do",
                    f'      url "{url}"',
                    f'      sha256 "{sha}"',
                    "    end",
                ]
            )
    lines.append("  end")
    return lines


def render_binary_formula(
    *,
    formula_stem: str,
    repository: str,
    version: str,
    target_assets: dict[str, tuple[str, str]],
    desc: str,
    homepage: str,
    license_id: str,
    binary_name: str,
    version_pattern: str,
    cargo_path: str | None = None,
    head_branch: str | None = "main",
) -> str:
    """Render a complete, brew-style-clean prebuilt-binary Ruby formula."""
    class_name = formula_class_name(formula_stem)
    by_os: dict[str, dict[str, tuple[str, str]]] = {"macos": {}, "linux": {}}
    for target, asset in target_assets.items():
        os_name, arch = SUPPORTED_TARGETS[target]
        by_os[os_name][arch] = asset

    lines: list[str] = [
        "# Written by ci-cd-release-infrastructure (update-homebrew-binary.yml).",
        "# Do not edit release URLs or checksums by hand.",
        f"class {class_name} < Formula",
        f'  desc "{desc}"',
        f'  homepage "{homepage}"',
    ]

    macos_only = bool(by_os["macos"]) and not by_os["linux"]
    if macos_only:
        mac = by_os["macos"]
        if "arm" in mac and "intel" in mac:
            arm_url, arm_sha = mac["arm"]
            intel_url, intel_sha = mac["intel"]
            lines.extend(
                [
                    "  if Hardware::CPU.arm?",
                    f'    url "{arm_url}"',
                    f'    sha256 "{arm_sha}"',
                    "  else",
                    f'    url "{intel_url}"',
                    f'    sha256 "{intel_sha}"',
                    "  end",
                ]
            )
        else:
            only_url, only_sha = next(iter(mac.values()))
            lines.extend([f'  url "{only_url}"', f'  sha256 "{only_sha}"'])
        lines.append(f'  version "{version}"')
        lines.append(f'  license "{license_id}"')
        if head_branch:
            lines.extend(
                [
                    "",
                    "  head do",
                    f'    url "https://github.com/{repository}.git", branch: "{head_branch}"',
                    '    depends_on "rust" => :build',
                    "  end",
                ]
            )
        lines.extend(["", "  depends_on :macos"])
    else:
        lines.append(f'  version "{version}"')
        lines.append(f'  license "{license_id}"')
        if head_branch:
            lines.extend(
                [
                    "",
                    "  head do",
                    f'    url "https://github.com/{repository}.git", branch: "{head_branch}"',
                    '    depends_on "rust" => :build',
                    "  end",
                ]
            )
        if by_os["macos"]:
            lines.append("")
            lines.extend(_render_os_block("macos", by_os["macos"]))
        if by_os["linux"]:
            lines.append("")
            lines.extend(_render_os_block("linux", by_os["linux"]))

    cargo_args = (
        f'*std_cargo_args(path: "{cargo_path}")' if cargo_path else "*std_cargo_args"
    )
    lines.append("")
    if head_branch:
        lines.extend(
            [
                "  def install",
                "    if build.head?",
                f'      system "cargo", "install", {cargo_args}',
                "    else",
                f'      bin.install "{binary_name}"',
                "    end",
                "  end",
            ]
        )
    else:
        lines.extend(
            [
                "  def install",
                f'    bin.install "{binary_name}"',
                "  end",
            ]
        )

    lines.extend(
        [
            "",
            "  test do",
            f'    assert_match(/{version_pattern}/, shell_output("#{{bin}}/{binary_name} --version"))',
            "  end",
            "end",
            "",
        ]
    )
    return "\n".join(lines)


def splice_binary_formula(
    formula_path: Path,
    version: str,
    target_assets: dict[str, tuple[str, str]],
) -> None:
    """Update ``version`` and each target's ``url`` + ``sha256`` in an existing formula.

    Exits loudly if any expected target block or the ``version`` line is missing.
    """
    if not formula_path.is_file():
        sys.exit(
            f"Formula not found: {formula_path}. Provide --desc to generate a new "
            "formula, or create the formula in the tap repository first."
        )

    content = formula_path.read_text(encoding="utf-8")
    missing: list[str] = []

    for target, (new_url, new_sha) in target_assets.items():
        pattern = re.compile(
            rf'^(?P<indent>[ \t]*)url\s+"[^"]*{re.escape(target)}[^"]*"\n'
            rf'(?P<sha_indent>[ \t]*)sha256\s+"[0-9a-fA-F]{{64}}"',
            re.MULTILINE,
        )

        def _replace(
            match: re.Match[str], url: str = new_url, sha: str = new_sha
        ) -> str:
            return (
                f'{match.group("indent")}url "{url}"\n'
                f'{match.group("sha_indent")}sha256 "{sha}"'
            )

        content, count = pattern.subn(_replace, content)
        if count != 1:
            missing.append(
                f'unique url/sha256 pair for target "{target}" (found {count})'
            )

    content, version_count = re.subn(
        r'^([ \t]*)version\s+"[^"]+"',
        lambda m: f'{m.group(1)}version "{version}"',
        content,
        flags=re.MULTILINE,
        count=1,
    )
    if version_count != 1:
        missing.append('`version "..."` declaration')

    if missing:
        sys.exit(
            f"Could not update {formula_path}; missing " + ", ".join(missing) + "."
        )

    formula_path.write_text(content, encoding="utf-8")


def write_binary_summary(
    *,
    formula_name: str,
    version: str,
    formula_path: str,
    mode: str,
    target_count: int,
) -> None:
    """Append a binary formula update summary to GITHUB_STEP_SUMMARY when in Actions."""
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if os.environ.get("GITHUB_ACTIONS") != "true" or not summary_path:
        return

    def cell(value: str) -> str:
        return (
            escape(value).replace("|", "&#124;").replace("\r", " ").replace("\n", " ")
        )

    summary = (
        "### Homebrew binary formula updated\n\n"
        "| Field | Value |\n| --- | --- |\n"
        f"| Formula | <code>{cell(formula_name)}</code> |\n"
        f"| Release | <code>{cell(version)}</code> |\n"
        f"| Path | <code>{cell(formula_path)}</code> |\n"
        f"| Mode | <code>{cell(mode)}</code> |\n"
        f"| Targets | {target_count} |\n\n"
    )
    try:
        with open(summary_path, "a", encoding="utf-8") as summary_file:
            summary_file.write(summary)
    except OSError as e:
        log(f"Could not write job summary: {e}", level="warning")


def _export_outputs(formula_name: str, rel_formula_path: str, version: str) -> None:
    github_output = os.environ.get("GITHUB_OUTPUT")
    if not github_output:
        return
    try:
        with open(github_output, "a", encoding="utf-8") as f:
            f.write(f"formula_name={formula_name}\n")
            f.write(f"formula_path={rel_formula_path}\n")
            f.write(f"version={version}\n")
    except OSError as e:
        log(f"Failed to write to GITHUB_OUTPUT: {e}", level="warning")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Render or splice a prebuilt-binary Homebrew formula from release artifacts."
    )
    parser.add_argument(
        "--version", required=True, help="Release version or tag (e.g., v1.2.3)"
    )
    parser.add_argument(
        "--repository",
        required=True,
        help="GitHub repository slug (e.g., owner/repo)",
    )
    parser.add_argument(
        "--formula-path",
        required=True,
        help="Formula path relative to --tap-dir (e.g., Formula/dark-matter.rb)",
    )
    parser.add_argument(
        "--tap-dir",
        required=True,
        type=Path,
        help="Path to the checked-out Homebrew tap repository",
    )
    parser.add_argument(
        "--artifacts-dir",
        required=True,
        type=Path,
        help="Directory holding the downloaded release archives and/or .sha256 files",
    )
    parser.add_argument(
        "--archive-template",
        required=True,
        help="Archive filename template using {version} and {target}",
    )
    parser.add_argument(
        "--targets",
        required=True,
        help="Comma-separated target triples (e.g., aarch64-apple-darwin,x86_64-apple-darwin)",
    )
    parser.add_argument(
        "--desc",
        default="",
        help="Formula description (enables full formula rendering instead of in-place splicing)",
    )
    parser.add_argument(
        "--homepage",
        default="",
        help="Formula homepage URL (defaults to https://github.com/<repository>)",
    )
    parser.add_argument(
        "--license",
        default="MIT",
        help="SPDX license identifier for rendered formulas (default: MIT)",
    )
    parser.add_argument(
        "--binary-name",
        default="",
        help="Binary filename installed by bin.install (defaults to formula stem)",
    )
    parser.add_argument(
        "--version-pattern",
        default="",
        help="Ruby regex body for `test do` matching `<binary> --version`",
    )
    parser.add_argument(
        "--cargo-path",
        default="",
        help="Optional Cargo workspace path for `head` builds",
    )
    parser.add_argument(
        "--head-branch",
        default="main",
        help="Branch for `head do` source builds (pass empty string to omit)",
    )

    args = parser.parse_args(argv)
    version = args.version.lstrip("v")
    tag = f"v{version}"
    targets = parse_targets(args.targets)

    rel_formula_path = str(Path(args.formula_path))
    resolved_formula = (args.tap_dir / rel_formula_path).resolve()
    tap_root = args.tap_dir.resolve()
    if not resolved_formula.is_relative_to(tap_root):
        sys.exit(f"Formula path '{args.formula_path}' escapes tap directory.")
    formula_stem = resolved_formula.stem

    with log_group(f"Resolving {len(targets)} release artifact checksums for {tag}"):
        target_assets: dict[str, tuple[str, str]] = {}
        for target in targets:
            archive = archive_filename(args.archive_template, version, target)
            sha = resolve_sha256(args.artifacts_dir, archive)
            url = release_url(args.repository, tag, archive)
            log(f"{target}: {archive} ({sha[:12]}...)")
            target_assets[target] = (url, sha)

    desc = args.desc.strip()
    mode = "render" if desc else "splice"
    with log_group(f"Updating {rel_formula_path} ({mode} mode)"):
        if desc:
            binary_name = args.binary_name.strip() or formula_stem
            homepage = args.homepage.strip() or f"https://github.com/{args.repository}"
            version_pattern = (
                args.version_pattern.strip()
                or rf"^{re.escape(binary_name)}(?: version)? \d+\.\d+\.\d+"
            )
            rendered = render_binary_formula(
                formula_stem=formula_stem,
                repository=args.repository,
                version=version,
                target_assets=target_assets,
                desc=desc,
                homepage=homepage,
                license_id=args.license.strip() or "MIT",
                binary_name=binary_name,
                version_pattern=version_pattern,
                cargo_path=args.cargo_path.strip() or None,
                head_branch=args.head_branch.strip() or None,
            )
            resolved_formula.parent.mkdir(parents=True, exist_ok=True)
            resolved_formula.write_text(rendered, encoding="utf-8")
        else:
            splice_binary_formula(resolved_formula, version, target_assets)

    _export_outputs(formula_stem, rel_formula_path, version)
    write_binary_summary(
        formula_name=formula_stem,
        version=tag,
        formula_path=rel_formula_path,
        mode=mode,
        target_count=len(target_assets),
    )
    log(f"Successfully updated {rel_formula_path} to {tag}.", level="success")


if __name__ == "__main__":
    run_logged(main)
