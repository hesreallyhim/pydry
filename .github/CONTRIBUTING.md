# Contributing to pydry

Thanks for your interest in contributing! Here's how to get started.

## Development Setup

Install Python 3.11 or newer and Go 1.25 or newer before setting up the project. `make check` uses Go to run the repository-pinned actionlint release.

```bash
# Clone the repo
git clone <repo-url>
cd pydry

# Create and activate a virtual environment
make venv
source venv/bin/activate

# Install the project with dev dependencies
make install

# Install pre-commit hooks
make pre-commit
```

## Development Workflow

1. Create a branch from `develop` for your change
2. Make your changes
3. Run the full check suite: `make check`
4. Commit with a clear message (conventional commits preferred)
5. Open a pull request against `develop`

## Running Checks

```bash
make lint          # Run ruff linter
make actionlint    # Lint GitHub Actions workflows
make format        # Auto-format with ruff
make typecheck     # Run mypy
make test          # Run pytest
make check         # Run all checks, including actionlint and pydry policy
```

## Code Style

- Code is formatted and linted with [ruff](https://docs.astral.sh/ruff/)
- Type hints are required (enforced by mypy in strict mode)
- Python 3.11+ features are encouraged

## Reporting Issues

- Use the **Bug Report** template for bugs
- Use the **Feature Request** template for enhancements
- Include reproduction steps and expected vs actual behavior

## Pull Requests

- Keep PRs focused on a single change
- Fill out the PR template checklist
- Ensure `make check` passes before requesting review
