set shell := ["bash", "-euc", "-o", "pipefail"]
set unstable
set quiet

# --- ANSI Colors ---

blue := '\033[1;34m'
green := '\033[1;32m'
nc := '\033[0m'

# Show available commands
default:
    @just --list

# Sync/install dependencies using uv
sync:
    uv sync --quiet

# Auto-format Python and Markdown
format: sync
    @printf "\n{{ blue }}=== Formatting Code ==={{ nc }}\n"
    uv run ruff check --fix .
    uv run ruff format .
    uv run rumdl fmt .
    @printf "{{ green }}✔ Formatting complete{{ nc }}\n"

# Run linters and check formatting (Ruff and Markdown)
lint: sync
    @printf "\n{{ blue }}=== Running Linters ==={{ nc }}\n"
    uv run ruff check .
    uv run ruff format --check .
    uv run rumdl check .
    uv run rumdl fmt --check .
    @printf "{{ green }}✔ Linting passed{{ nc }}\n"

# Run static type checking with Mypy
typecheck: sync
    @printf "\n{{ blue }}=== Running Type Checks ==={{ nc }}\n"
    uv run mypy .
    @printf "{{ green }}✔ Type checking passed{{ nc }}\n"

# Run pytest
test: sync
    @printf "\n{{ blue }}=== Running Tests ==={{ nc }}\n"
    uv run pytest
    @printf "{{ green }}✔ All tests passed{{ nc }}\n"

# Run the exact pipeline executed by CI
ci: lint typecheck test
    @printf "\n{{ green }}✔ Local CI pipeline completed successfully. Clear to push!{{ nc }}\n"

# Remove caches, artifacts, and temp files
clean:
    @printf "\n{{ blue }}=== Cleaning Workspace ==={{ nc }}\n"
    rm -rf \
        .pytest_cache \
        .mypy_cache \
        .ruff_cache \
        .cache
    find . -type d -name "__pycache__" -exec rm -rf {} +
    @printf "{{ green }}✔ Workspace cleaned{{ nc }}\n"
