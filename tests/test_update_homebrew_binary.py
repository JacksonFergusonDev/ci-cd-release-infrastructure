import hashlib
from pathlib import Path

import pytest

from scripts import update_homebrew_binary as binary


def test_formula_class_name() -> None:
    assert binary.formula_class_name("dark-matter") == "DarkMatter"
    assert binary.formula_class_name("parallax_cli") == "ParallaxCli"
    with pytest.raises(ValueError, match="Invalid formula name"):
        binary.formula_class_name("---")


def test_parse_targets_validation() -> None:
    assert binary.parse_targets("aarch64-apple-darwin, x86_64-apple-darwin") == [
        "aarch64-apple-darwin",
        "x86_64-apple-darwin",
    ]

    with pytest.raises(SystemExit, match="At least one target"):
        binary.parse_targets(" , ")

    with pytest.raises(SystemExit, match="Unsupported target"):
        binary.parse_targets("x86_64-pc-windows-msvc")

    with pytest.raises(SystemExit, match="Duplicate target"):
        binary.parse_targets("aarch64-apple-darwin,aarch64-apple-darwin")


def test_archive_filename() -> None:
    assert (
        binary.archive_filename(
            "dark-matter-v{version}-{target}.tar.gz", "0.2.0", "aarch64-apple-darwin"
        )
        == "dark-matter-v0.2.0-aarch64-apple-darwin.tar.gz"
    )
    with pytest.raises(SystemExit, match="Unknown placeholder"):
        binary.archive_filename("{bad}", "0.2.0", "aarch64-apple-darwin")


def test_resolve_sha256_modes_and_errors(tmp_path: Path) -> None:
    archive = "demo-v1.0.0-aarch64-apple-darwin.tar.gz"
    payload = b"binary-archive-bytes"
    expected = hashlib.sha256(payload).hexdigest()

    # Missing both fails
    with pytest.raises(SystemExit, match="Missing release artifact"):
        binary.resolve_sha256(tmp_path, archive)

    # Archive only
    (tmp_path / archive).write_bytes(payload)
    assert binary.resolve_sha256(tmp_path, archive) == expected

    # Both archive and matching .sha256
    sha_file = tmp_path / f"{archive}.sha256"
    sha_file.write_text(f"{expected.upper()}  {archive}\n", encoding="utf-8")
    assert binary.resolve_sha256(tmp_path, archive) == expected

    # Mismatch between archive and .sha256 fails
    sha_file.write_text(f"{'0' * 64}  {archive}\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="SHA256 mismatch"):
        binary.resolve_sha256(tmp_path, archive)

    # Checksum file only
    (tmp_path / archive).unlink()
    sha_file.write_text(f"{expected}  {archive}\n", encoding="utf-8")
    assert binary.resolve_sha256(tmp_path, archive) == expected

    # Invalid checksum format fails
    sha_file.write_text("not-a-sha\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="Invalid SHA256"):
        binary.resolve_sha256(tmp_path, archive)


def test_render_binary_formula_macos_and_linux() -> None:
    assets = {
        "aarch64-apple-darwin": ("https://example/mac-arm.tar.gz", "a" * 64),
        "x86_64-apple-darwin": ("https://example/mac-x64.tar.gz", "b" * 64),
        "aarch64-unknown-linux-gnu": ("https://example/lin-arm.tar.gz", "c" * 64),
        "x86_64-unknown-linux-gnu": ("https://example/lin-x64.tar.gz", "d" * 64),
    }
    formula = binary.render_binary_formula(
        formula_stem="dark-matter",
        repository="JacksonFergusonDev/dark-matter",
        version="0.2.0",
        target_assets=assets,
        desc="Dependency-graph-aware storage profiler for Homebrew",
        homepage="https://github.com/JacksonFergusonDev/dark-matter",
        license_id="MIT",
        binary_name="dark-matter",
        version_pattern=r"^dark-matter version \d+\.\d+\.\d+",
    )

    assert "class DarkMatter < Formula" in formula
    assert 'version "0.2.0"' in formula
    # head must precede on_macos per Homebrew FormulaAudit/ComponentsOrder
    assert formula.index("head do") < formula.index("on_macos do")
    assert formula.index("on_macos do") < formula.index("on_linux do")
    assert 'url "https://example/mac-arm.tar.gz"' in formula
    assert 'url "https://example/lin-x64.tar.gz"' in formula
    assert 'bin.install "dark-matter"' in formula
    assert (
        'assert_match(/^dark-matter version \\d+\\.\\d+\\.\\d+/, shell_output("#{bin}/dark-matter --version"))'
        in formula
    )


def test_render_binary_formula_macos_only_and_variants() -> None:
    # Two macOS targets with cargo_path
    formula = binary.render_binary_formula(
        formula_stem="demo-cli",
        repository="org/demo",
        version="1.0.0",
        target_assets={
            "aarch64-apple-darwin": ("https://example/arm.tar.gz", "a" * 64),
            "x86_64-apple-darwin": ("https://example/x64.tar.gz", "b" * 64),
        },
        desc="Demo CLI",
        homepage="https://example.com",
        license_id="GPL-3.0-only",
        binary_name="demo-cli",
        version_pattern=r"^demo-cli \d+\.\d+\.\d+",
        cargo_path="crates/cli",
    )
    assert "if Hardware::CPU.arm?" in formula
    assert "depends_on :macos" in formula
    assert '*std_cargo_args(path: "crates/cli")' in formula

    # Single macOS target with head disabled
    single = binary.render_binary_formula(
        formula_stem="demo-cli",
        repository="org/demo",
        version="1.0.0",
        target_assets={
            "aarch64-apple-darwin": ("https://example/arm.tar.gz", "a" * 64),
        },
        desc="Demo CLI",
        homepage="https://example.com",
        license_id="MIT",
        binary_name="demo-cli",
        version_pattern=r"^demo-cli",
        head_branch=None,
    )
    assert "if Hardware::CPU.arm?" not in single
    assert "head do" not in single
    assert "if build.head?" not in single
    assert 'url "https://example/arm.tar.gz"' in single


def test_splice_binary_formula_updates_parallax_style(tmp_path: Path) -> None:
    formula_path = tmp_path / "parallax-cli.rb"
    formula_path.write_text(
        """\
class ParallaxCli < Formula
  desc "Manage your Parallax library and Kobo from the terminal"
  homepage "https://parallax.jacksonferguson.me"
  if Hardware::CPU.arm?
    url "https://github.com/JacksonFergusonDev/parallax/releases/download/v0.1.3/parallax-cli-0.1.3-aarch64-apple-darwin.tar.gz"
    sha256 "05b09599b5c64cba79a725770197f66dee69c800d25dfedb571e1a763c100e15"
  else
    url "https://github.com/JacksonFergusonDev/parallax/releases/download/v0.1.3/parallax-cli-0.1.3-x86_64-apple-darwin.tar.gz"
    sha256 "e426ae276008ed0eabbf3dcc53e76fa4df236779fc49e994b1df75259396ce05"
  end
  version "0.1.3"
  license "GPL-3.0-only"

  def install
    libexec.install "parallax-cli", "kepubify"
  end
end
""",
        encoding="utf-8",
    )

    binary.splice_binary_formula(
        formula_path,
        "0.2.0",
        {
            "aarch64-apple-darwin": (
                "https://github.com/JacksonFergusonDev/parallax/releases/download/v0.2.0/parallax-cli-0.2.0-aarch64-apple-darwin.tar.gz",
                "1" * 64,
            ),
            "x86_64-apple-darwin": (
                "https://github.com/JacksonFergusonDev/parallax/releases/download/v0.2.0/parallax-cli-0.2.0-x86_64-apple-darwin.tar.gz",
                "2" * 64,
            ),
        },
    )

    updated = formula_path.read_text(encoding="utf-8")
    assert 'version "0.2.0"' in updated
    assert "parallax-cli-0.2.0-aarch64-apple-darwin.tar.gz" in updated
    assert f'sha256 "{"1" * 64}"' in updated
    assert "parallax-cli-0.2.0-x86_64-apple-darwin.tar.gz" in updated
    assert f'sha256 "{"2" * 64}"' in updated
    assert 'libexec.install "parallax-cli", "kepubify"' in updated


def test_splice_binary_formula_errors(tmp_path: Path) -> None:
    missing_path = tmp_path / "missing.rb"
    with pytest.raises(SystemExit, match="Formula not found"):
        binary.splice_binary_formula(missing_path, "1.0.0", {})

    bad_formula = tmp_path / "bad.rb"
    bad_formula.write_text("class Bad < Formula\nend\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="Could not update"):
        binary.splice_binary_formula(
            bad_formula,
            "1.0.0",
            {"aarch64-apple-darwin": ("https://example/a.tar.gz", "a" * 64)},
        )


def test_main_render_and_splice_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tap_dir = tmp_path / "tap"
    (tap_dir / "Formula").mkdir(parents=True)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    github_output = tmp_path / "gh_output.txt"
    summary_file = tmp_path / "gh_summary.md"

    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_OUTPUT", str(github_output))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_file))

    for target, data in [
        ("aarch64-apple-darwin", b"mac-arm"),
        ("x86_64-apple-darwin", b"mac-x64"),
        ("aarch64-unknown-linux-gnu", b"lin-arm"),
        ("x86_64-unknown-linux-gnu", b"lin-x64"),
    ]:
        (artifacts / f"dark-matter-v0.2.0-{target}.tar.gz").write_bytes(data)

    # Render mode creates Formula/dark-matter.rb
    binary.main(
        [
            "--version",
            "v0.2.0",
            "--repository",
            "JacksonFergusonDev/dark-matter",
            "--formula-path",
            "Formula/dark-matter.rb",
            "--tap-dir",
            str(tap_dir),
            "--artifacts-dir",
            str(artifacts),
            "--archive-template",
            "dark-matter-v{version}-{target}.tar.gz",
            "--targets",
            "aarch64-apple-darwin,x86_64-apple-darwin,aarch64-unknown-linux-gnu,x86_64-unknown-linux-gnu",
            "--desc",
            "Dependency-graph-aware storage profiler for Homebrew",
            "--license",
            "MIT",
        ]
    )

    rendered = (tap_dir / "Formula/dark-matter.rb").read_text(encoding="utf-8")
    assert 'version "0.2.0"' in rendered
    assert hashlib.sha256(b"mac-arm").hexdigest() in rendered
    assert "formula_name=dark-matter" in github_output.read_text(encoding="utf-8")
    assert "Mode | <code>render</code>" in summary_file.read_text(encoding="utf-8")

    # Now splice mode bumps the rendered formula to 0.3.0
    for target, data in [
        ("aarch64-apple-darwin", b"mac-arm-3"),
        ("x86_64-apple-darwin", b"mac-x64-3"),
        ("aarch64-unknown-linux-gnu", b"lin-arm-3"),
        ("x86_64-unknown-linux-gnu", b"lin-x64-3"),
    ]:
        (artifacts / f"dark-matter-v0.3.0-{target}.tar.gz").write_bytes(data)

    binary.main(
        [
            "--version",
            "0.3.0",
            "--repository",
            "JacksonFergusonDev/dark-matter",
            "--formula-path",
            "Formula/dark-matter.rb",
            "--tap-dir",
            str(tap_dir),
            "--artifacts-dir",
            str(artifacts),
            "--archive-template",
            "dark-matter-v{version}-{target}.tar.gz",
            "--targets",
            "aarch64-apple-darwin,x86_64-apple-darwin,aarch64-unknown-linux-gnu,x86_64-unknown-linux-gnu",
        ]
    )

    spliced = (tap_dir / "Formula/dark-matter.rb").read_text(encoding="utf-8")
    assert 'version "0.3.0"' in spliced
    assert hashlib.sha256(b"lin-x64-3").hexdigest() in spliced
    assert "Mode | <code>splice</code>" in summary_file.read_text(encoding="utf-8")


def test_main_rejects_path_traversal(tmp_path: Path) -> None:
    tap_dir = tmp_path / "tap"
    tap_dir.mkdir()
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    with pytest.raises(SystemExit, match="escapes tap directory"):
        binary.main(
            [
                "--version",
                "v1.0.0",
                "--repository",
                "org/repo",
                "--formula-path",
                "../escape.rb",
                "--tap-dir",
                str(tap_dir),
                "--artifacts-dir",
                str(artifacts),
                "--archive-template",
                "repo-{version}-{target}.tar.gz",
                "--targets",
                "aarch64-apple-darwin",
            ]
        )
