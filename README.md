<!-- rumdl-disable-file first-line-heading -->
<div align="center">

# CI/CD & Release Infrastructure

[![CI](https://img.shields.io/github/actions/workflow/status/JacksonFergusonDev/ci-cd-release-infrastructure/ci.yml?style=flat-square&color=white&labelColor=black&label=CI)](https://github.com/JacksonFergusonDev/ci-cd-release-infrastructure/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.14+-white?style=flat-square&color=white&labelColor=black)](https://www.python.org/downloads/)
[![Ruff](https://img.shields.io/badge/style-ruff-white?style=flat-square&color=white&labelColor=black)](https://github.com/astral-sh/ruff)
[![Mypy](https://img.shields.io/badge/mypy-checked-white?style=flat-square&color=white&labelColor=black)](https://mypy-lang.org/)
[![prek](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/j178/prek/master/docs/assets/badge-v0.json&style=flat-square&color=white&labelColor=black)](https://github.com/j178/prek)
[![License](https://img.shields.io/badge/license-MIT-white?style=flat-square&color=white&labelColor=black)](LICENSE)

</div>

Centralized infrastructure repository for reusable GitHub Actions workflows and deployment automation scripts. By decoupling pipeline logic and release management from application code, this repository acts as a single source of truth across projects.

---

## 🛠 Repository Contents

```text
.
├── .github/
│   ├── actions/
│   │   ├── update-formula-binary/ # Internal step for update-homebrew-binary.yml
│   │   ├── update-formula-local/  # Internal step for update-homebrew-local.yml
│   │   └── update-formula-pypi/   # Internal step for update-homebrew.yml
│   └── workflows/
│       ├── ci.yml                     # Internal verification pipeline
│       ├── update-homebrew.yml        # Reusable workflow for PyPI-published packages
│       ├── update-homebrew-binary.yml # Reusable workflow for prebuilt-binary releases
│       └── update-homebrew-local.yml  # Reusable workflow using caller repository manifests
├── scripts/
│   ├── _brew_utils.py             # Shared PyPI querying and Homebrew splicing logic
│   ├── release.py                 # Turnkey atomic release orchestrator (uv & Cargo)
│   ├── update_homebrew.py         # PyPI polling & Homebrew formula dependency splicing
│   ├── update_homebrew_binary.py  # Prebuilt-binary Homebrew formula renderer & splicer
│   └── update_homebrew_local.py   # Manifest-based Homebrew formula dependency splicing
└── tests/
    ├── test_brew_utils.py         # Unit tests for shared Homebrew utilities
    ├── test_release.py            # Unit tests for release orchestration & rollback
    ├── test_release_git.py        # Integration tests against real git, uv, and cargo
    ├── test_update_homebrew.py    # Unit tests for PyPI synchronization
    ├── test_update_homebrew_binary.py # Unit tests for prebuilt-binary formula updates
    └── test_update_homebrew_local.py # Unit tests for local manifest synchronization
```

---

## 🔄 Reusable Workflows

Each workflow runs its scripts through a composite action referenced with GitHub's `$/` self-repository syntax, so the scripts always come from the same commit as the workflow you call. Pinning the workflow (`@<commit-sha>`) therefore pins everything it executes; `@main` tracks the latest changes.

### 1. Homebrew Tap Updater (`update-homebrew.yml`)

Automates synchronization of a Homebrew formula when releasing a package published to PyPI. It polls PyPI until the release is visible, extracts the source distribution checksum, pins every Python resource to the version in the caller's `uv.lock` at the release tag, and splices those resources into the target Ruby formula before running `brew audit`. Homebrew users therefore install exactly the dependency versions the caller's CI tested, and the caller must commit a `uv.lock` that is current with its `pyproject.toml`.

**Caller Workflow Usage:**

```yaml
jobs:
  sync-homebrew:
    name: Delegate Homebrew Update
    needs: build-n-publish # Ensure PyPI publishing succeeds first
    uses: JacksonFergusonDev/ci-cd-release-infrastructure/.github/workflows/update-homebrew.yml@main
    with:
      tag: ${{ github.ref_name }}
      # Optional: automatically inferred from pyproject.toml and defaulted to Formula/<package_name>.rb
      # package_name: "target-package"
      # formula_path: "Formula/target-package.rb"
      # dry_run: true  # audit only; skip commit & push
    secrets:
      TAP_GITHUB_TOKEN: ${{ secrets.TAP_GITHUB_TOKEN }}
```

### 2. Local Manifest Homebrew Tap Updater (`update-homebrew-local.yml`)

Designed for projects that do not publish source distributions directly to PyPI or rely on local manifest exports (e.g., using `uv export`). Extracts release tarball checksums directly from GitHub releases, builds local dependency blocks, and commits the updated formula to your tap repository.

**Caller Workflow Usage:**

```yaml
jobs:
  sync-homebrew-local:
    name: Sync Local Homebrew Formula
    needs: release
    uses: JacksonFergusonDev/ci-cd-release-infrastructure/.github/workflows/update-homebrew-local.yml@main
    with:
      tag: ${{ github.ref_name }}
      # Optional: defaults to Formula/<package_name>.rb based on caller pyproject.toml
      # formula_path: "Formula/target-cli.rb"
      # dry_run: true  # audit only; skip commit & push
    secrets:
      TAP_GITHUB_TOKEN: ${{ secrets.TAP_GITHUB_TOKEN }}
```

### 3. Prebuilt Binary Homebrew Tap Updater (`update-homebrew-binary.yml`)

Updates a Homebrew formula from prebuilt release archive artifacts (`.tar.gz` and/or `.sha256`) uploaded earlier in the caller's release workflow. Runs on `ubuntu-24.04-arm` and supports two modes:

- **Render mode** (`desc` set): generates a complete, `brew style`-clean prebuilt-binary formula (`on_macos` / `on_linux` + `on_arm` / `on_intel`, or macOS-only `Hardware::CPU.arm?`) with optional `head do` Cargo builds.
- **Splice mode** (`desc` omitted): updates `version` and each target's `url` and `sha256` in place inside an existing tap formula, preserving custom `head`, `install`, and `test` blocks.

**Caller Workflow Usage (Render Mode):**

```yaml
jobs:
  sync-homebrew:
    name: Update Homebrew formula
    needs: [verify, build-binaries, github-release]
    uses: JacksonFergusonDev/ci-cd-release-infrastructure/.github/workflows/update-homebrew-binary.yml@main
    with:
      tag: v${{ needs.verify.outputs.version }}
      formula_path: Formula/my-cli.rb
      artifact_pattern: bin-*
      archive_template: my-cli-v{version}-{target}.tar.gz
      targets: aarch64-apple-darwin,x86_64-apple-darwin,aarch64-unknown-linux-gnu,x86_64-unknown-linux-gnu
      desc: Fast CLI tool
      license: MIT
      dry_run: ${{ github.event_name != 'push' }}
    secrets:
      TAP_GITHUB_TOKEN: ${{ secrets.TAP_GITHUB_TOKEN }}
```

### Testing Workflow Changes

Both workflows accept `dry_run: true`, which runs every step through `brew audit` but skips the commit and push, writing the formula diff to the job summary instead. To exercise a branch of this repository end to end without touching the tap, point a caller's `workflow_dispatch` job at the branch:

```yaml
on: workflow_dispatch

jobs:
  test-homebrew-sync:
    uses: JacksonFergusonDev/ci-cd-release-infrastructure/.github/workflows/update-homebrew.yml@<branch>
    with:
      tag: v1.2.3  # an existing release
      dry_run: true
    secrets:
      TAP_GITHUB_TOKEN: ${{ secrets.TAP_GITHUB_TOKEN }}
```

Because the scripts resolve to the workflow's own commit, this runs the branch's scripts as well as its workflow.

---

## 📜 Automation Scripts & Remote Execution

The repository provides release and deployment automation tools under `scripts/`. In particular, `release.py` is structured as a standalone script featuring a [PEP 723](https://peps.python.org/pep-0723/) inline metadata block so caller projects can execute it directly via `uv run` without cloning this repository or installing local dependencies.

The Homebrew tap update scripts (`update_homebrew.py` and `update_homebrew_local.py`) are modular internal scripts backed by `_brew_utils.py` and invoked automatically by the reusable GitHub Actions workflows. Both take their versions from the caller's `uv.lock` (`uv export --locked`), so a stale lockfile fails the run instead of being re-resolved, and only evaluate environment markers for Homebrew's install target rather than the CI runner: macOS (`aarch64-apple-darwin`) and the Python version from the formula's `depends_on "python@X.Y"`. A formula missing its top-level `url`/`sha256` or the `# RESOURCE_BLOCK_START`/`# RESOURCE_BLOCK_END` sentinels fails the run instead of being left unchanged.

They detect `GITHUB_ACTIONS=true` to emit collapsible log groups, warning/error annotations, and a job summary with the package, release, formula path, and resource count. Local runs use plain status prefixes. Progress messages are flushed immediately; job summaries describe formula generation, with audit and push handled by subsequent workflow steps.

### Atomic Release Orchestrator (`scripts/release.py`)

Output uses numbered steps, aligned status prefixes, and color when writing to a terminal. Redirected output stays plain text; set `NO_COLOR` to disable color. Errors and rollback messages are written to stderr.

A turnkey, atomic release orchestrator for uv-based Python and Cargo Rust projects (including Cargo workspaces and hybrid Rust/maturin repos). Executes a two-phase workflow:

1. **Phase 1: Pre-Flight Checks (Read-Only)**
   - Detects the project manifest (`pyproject.toml` with static `[project].version`, or `Cargo.toml` with `[package].version` / `[workspace.package].version`).
   - Verifies tool prerequisites (`git`, plus `uv` or `cargo`).
   - Ensures the active branch matches `--branch` (default: `main`).
   - Rejects dirty working directories with uncommitted or untracked changes.
   - Verifies synchronization with remote (`git fetch` and hash comparison).
   - Validates the manifest and SemVer structure.
   - Checks for local and remote tag collisions (`git ls-remote`).
   - Executes any optional `--pre-flight` commands.
1. **Phase 2: Transactional Execution & Rollback**
   - Updates the version in `pyproject.toml` or `Cargo.toml`.
   - Updates `uv.lock` (`uv lock`) or `Cargo.lock` (`cargo update --workspace --offline --quiet`), committed unless the lockfile is gitignored.
   - Stages and creates release commit.
   - Creates annotated tag.
   - Atomically pushes only the release branch and the new tag (`git push --atomic origin HEAD:refs/heads/<branch> refs/tags/<tag>`), unless `--no-push` is specified. Other local tags are never pushed.
   - Automatically rolls back (deletes the tag, resets to the starting commit, and restores the manifest and lockfile byte-for-byte, removing a lockfile the release created) if any step fails or is aborted via `Ctrl+C`. If a rollback step fails, it says so and names the commit to restore manually.

#### Flags & Options

- `part` *(required)*: SemVer part to increment (`major`, `minor`, `patch`). Bumping a prerelease to the release it precedes finalizes it (`1.0.0-rc.1` + `patch` → `1.0.0`).
- `--branch <name>`: Expected release branch (default: `main`).
- `--remote <name>`: Git remote target (default: `origin`).
- `--dry-run`: Validates pre-flight checks and previews the candidate version without mutating repository state.
- `--no-push`: Commits and tags locally without pushing to the remote.
- `--pre-flight <cmd>`: Shell command to execute during pre-flight checks (can be specified multiple times).

#### Remote Usage in `justfile`

```just
# Bump project version (part: major, minor, patch), sync lockfile, commit, tag, and atomic push
bump part:
    uv run https://raw.githubusercontent.com/JacksonFergusonDev/ci-cd-release-infrastructure/<commit-sha>/scripts/release.py {{ part }}
```

Pin the URL to a commit SHA so the script that commits, tags, and pushes your project only changes when you choose to update it. Replacing `<commit-sha>` with `refs/heads/main` (plus `--refresh`) follows the latest version instead.

---

## Contact

[![GitHub](https://img.shields.io/badge/GitHub-100000?style=for-the-badge&logo=github&logoColor=white)](https://github.com/JacksonFergusonDev)
[![LinkedIn](https://img.shields.io/badge/LinkedIn-0077B5?style=for-the-badge&logo=linkedin&logoColor=white)](https://www.linkedin.com/in/jackson--ferguson/)
[![Email](https://img.shields.io/badge/Email-D14836?style=for-the-badge&logo=gmail&logoColor=white)](mailto:jackson.ferguson0@gmail.com)

[![Website](https://raw.githubusercontent.com/JacksonFergusonDev/JacksonFergusonDev.github.io/refs/heads/main/.github/assets/badge.svg)](https://jacksonferguson.me)
