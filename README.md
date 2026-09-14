<!-- rumdl-disable-file first-line-heading -->
<div align="center">

# CI/CD & Release Infrastructure

[![CI](https://img.shields.io/github/actions/workflow/status/JacksonFergusonDev/ci-cd-tooling/ci.yml?style=flat-square&color=white&labelColor=black&label=CI)](https://github.com/JacksonFergusonDev/ci-cd-tooling/actions/workflows/ci.yml)
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
├── .github/workflows/
│   ├── ci.yml                     # Internal verification pipeline
│   ├── update-homebrew.yml        # Reusable workflow for PyPI-published packages
│   └── update-homebrew-local.yml  # Reusable workflow using caller repository manifests
├── scripts/
│   ├── brew_utils.py              # Shared PyPI querying and Homebrew splicing logic
│   ├── release.py                 # Turnkey atomic release orchestrator
│   ├── update_homebrew.py         # PyPI polling & Homebrew formula dependency splicing
│   └── update_homebrew_local.py   # Manifest-based Homebrew formula dependency splicing
└── tests/
    ├── test_brew_utils.py         # Unit tests for shared Homebrew utilities
    ├── test_release.py            # Unit tests for release orchestration & rollback
    ├── test_update_homebrew.py    # Unit tests for PyPI synchronization
    └── test_update_homebrew_local.py # Unit tests for local manifest synchronization
```

---

## 🔄 Reusable Workflows

### 1. Homebrew Tap Updater (`update-homebrew.yml`)

Automates synchronization of a Homebrew formula when releasing a package published to PyPI. It polls PyPI until the release is visible, extracts the source distribution checksum, resolves the full dependency tree via `uv pip compile`, and splices Python resources directly into the target Ruby formula before running `brew audit`.

**Caller Workflow Usage:**

```yaml
jobs:
  sync-homebrew:
    name: Delegate Homebrew Update
    needs: build-n-publish # Ensure PyPI publishing succeeds first
    uses: JacksonFergusonDev/ci-cd-tooling/.github/workflows/update-homebrew.yml@main
    with:
      tag: ${{ github.ref_name }}
      # Optional: automatically inferred from pyproject.toml and defaulted to Formula/<package_name>.rb
      # package_name: "target-package"
      # formula_path: "Formula/target-package.rb"
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
    uses: JacksonFergusonDev/ci-cd-tooling/.github/workflows/update-homebrew-local.yml@main
    with:
      tag: ${{ github.ref_name }}
      # Optional: defaults to Formula/<package_name>.rb based on caller pyproject.toml
      # formula_path: "Formula/target-cli.rb"
    secrets:
      TAP_GITHUB_TOKEN: ${{ secrets.TAP_GITHUB_TOKEN }}
```

---

## 📜 Automation Scripts & Remote Execution

The repository provides release and deployment automation tools under `scripts/`. In particular, `release.py` is structured as a standalone script featuring a [PEP 723](https://peps.python.org/pep-0723/) inline metadata block so caller projects can execute it directly via `uv run` without cloning this repository or installing local dependencies.

The Homebrew tap update scripts (`update_homebrew.py` and `update_homebrew_local.py`) are modular internal scripts backed by `brew_utils.py` and invoked automatically by the reusable GitHub Actions workflows.

### Atomic Release Orchestrator (`scripts/release.py`)

A turnkey, atomic release orchestrator for uv-based Python projects. Executes a two-phase workflow:

1. **Phase 1: Pre-Flight Checks (Read-Only)**
   - Verifies tool prerequisites (`git`, `uv`).
   - Ensures the active branch matches `--branch` (default: `main`).
   - Rejects dirty working directories with uncommitted or untracked changes.
   - Verifies synchronization with remote (`git fetch` and hash comparison).
   - Validates `pyproject.toml` and SemVer structure.
   - Checks for local and remote tag collisions (`git ls-remote`).
   - Executes any optional `--pre-flight` commands.
1. **Phase 2: Transactional Execution & Rollback**
   - Updates `[project.version]` in `pyproject.toml`.
   - Synchronizes `uv.lock` via `uv sync` and checks lock integrity (`uv lock --check`).
   - Stages and creates release commit.
   - Creates annotated tag.
   - Atomically pushes branch and tag to remote (`git push origin HEAD --tags --atomic`), unless `--no-push` is specified.
   - Automatically rolls back (deletes tag, runs `git reset --hard`) if any step fails or is aborted via `Ctrl+C`.

#### Flags & Options

- `part` *(required)*: SemVer part to increment (`major`, `minor`, `patch`).
- `--branch <name>`: Expected release branch (default: `main`).
- `--remote <name>`: Git remote target (default: `origin`).
- `--dry-run`: Validates pre-flight checks and previews the candidate version without mutating repository state.
- `--no-push`: Commits and tags locally without pushing to the remote.
- `--pre-flight <cmd>`: Shell command to execute during pre-flight checks (can be specified multiple times).

#### Remote Usage in `justfile`

```just
# Bump project version (part: major, minor, patch), sync lockfile, commit, tag, and atomic push
bump part:
    uv run --refresh https://raw.githubusercontent.com/JacksonFergusonDev/ci-cd-tooling/refs/heads/main/scripts/release.py {{ part }}
```
