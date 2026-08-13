# GitHub Actions integration

The pydry action runs `pydry check` against a checked-out repository, writes a JSON report, adds findings to the GitHub Actions log as annotations, and fails the job when the configured policy is violated.

## Add the check to a repository

Create `.github/workflows/pydry.yml` in the repository you want to check:

```yaml
name: pydry

on:
  pull_request:
  merge_group:

permissions:
  contents: read

jobs:
  pydry:
    name: pydry
    runs-on: ubuntu-latest
    steps:
      - name: Check out the repository
        uses: actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0 # v7.0.0

      - name: Check duplication policy
        uses: hesreallyhim/pydry@<40-character-release-commit-sha>
```

Replace `<40-character-release-commit-sha>` with the full commit SHA for the pydry release you have reviewed. A tag such as `v1` is convenient but mutable; pinning an immutable commit prevents an upstream tag change from silently changing code that runs in CI. Dependabot or Renovate can be used to propose reviewed pin updates.

The `merge_group` trigger is necessary when the repository uses GitHub merge queues. Without it, the required pydry check will not run for the temporary merge-group commit. The workflow needs only `contents: read`; do not grant write permissions to this analysis job. Prefer `pull_request` to `pull_request_target`, especially for contributions from forks.

After the workflow has run at least once, configure the job named `pydry` as a required status check in the branch ruleset or branch-protection rule. Keep the job name stable: renaming it also changes the status-check name GitHub expects.

## Configure policy

Project-wide settings live under `[tool.pydry]` in `pyproject.toml`. Configuration keys use underscores:

```toml
[tool.pydry]
threshold = 0.85
top_k = 100
top_level_only = false
strict = true
normalize_local_names = true
normalize_constants = false

max_exact_groups = 0
max_near_matches = "none"
max_abstract_candidates = 10
fail_on_scan_errors = true
fail_on_plugin_errors = true
annotation_limit = 25
```

The `max_*` settings are policy ceilings. A result above a configured ceiling is a violation; use the string `"none"` to leave a category unenforced. The diagnostic settings determine whether scan or plugin errors also violate policy. `top_k` limits the detailed near-match and abstraction rows retained in the JSON report, but policy counts always evaluate every match. `annotation_limit` bounds the workflow commands emitted by pydry; GitHub may impose a lower display limit.

When a key is omitted, pydry uses these built-in defaults:

| Setting | Default |
| --- | --- |
| `root` | `.` |
| `threshold` | `0.8` |
| `top_k` | `200` |
| `top_level_only` | `false` |
| `strict` | `true` |
| `normalize_local_names` | `true` |
| `normalize_constants` | `true` |
| `max_exact_groups` | `0` |
| `max_near_matches` | `"none"` |
| `max_abstract_candidates` | `0` |
| `fail_on_scan_errors` | `true` |
| `fail_on_plugin_errors` | `true` |
| `annotation_limit` | `10` |

Commit the policy with the source it governs. To require review when policy or CI wiring changes, add entries such as these to `.github/CODEOWNERS`:

```text
/pyproject.toml                 @your-org/code-quality
/.github/workflows/pydry.yml   @your-org/code-quality
```

Enable code-owner approval in the branch ruleset so that CODEOWNERS is enforced rather than merely informational.

## Override settings in a workflow

Every policy input is optional. A blank input defers to `pyproject.toml`; a nonblank input overrides the corresponding `[tool.pydry]` value for that run. This includes `root`, which is blank by default so that `tool.pydry.root` can select the scanned directory. If the repository has no `pyproject.toml`, set `config: ""` to use pydry's built-in defaults.

```yaml
- name: Check duplication policy
  uses: hesreallyhim/pydry@<40-character-release-commit-sha>
  with:
    root: src
    config: pyproject.toml
    report: reports/pydry-report.json
    python-version: "3.12"
    threshold: "0.88"
    max-exact-groups: "0"
    max-near-matches: "10"
    max-abstract-candidates: "5"
    fail-on-scan-errors: "true"
    annotation-limit: "20"
```

Boolean overrides accept `true` or `false`. Leaving them blank preserves the project configuration.

| Input | Default | Purpose |
| --- | --- | --- |
| `root` | blank | Directory to scan; defers to `[tool.pydry]` and then `.` |
| `config` | `pyproject.toml` | TOML file containing `[tool.pydry]`; blank omits `--config` |
| `report` | `.pydry/pydry-report.json` | JSON report destination |
| `python-version` | `3.11` | Python runtime for the action |
| `threshold` | blank | Similarity threshold |
| `top-k` | blank | Detailed near-match and abstraction rows retained in the report |
| `top-level-only` | blank | Ignore nested functions and methods |
| `strict` | blank | Stop analysis on scan errors |
| `normalize-local-names` | blank | Normalize local variable names for exact matching |
| `normalize-constants` | blank | Normalize literal values for exact matching |
| `max-exact-groups` | blank | Exact-duplicate group ceiling, or `none` |
| `max-near-matches` | blank | Near-match ceiling, or `none` |
| `max-abstract-candidates` | blank | Abstraction-candidate ceiling, or `none` |
| `fail-on-scan-errors` | blank | Treat scan diagnostics as policy failures |
| `fail-on-plugin-errors` | blank | Treat plugin diagnostics as policy failures |
| `annotation-limit` | blank | Maximum annotations written to the job log |

Paths are resolved in the checked-out repository workspace. pydry creates missing parent directories for a custom report path.

## Outputs and exit status

The action exposes these string outputs:

| Output | Meaning |
| --- | --- |
| `result` | `pass` or `fail` after analysis completes |
| `report` | Path supplied through the `report` input |
| `exact-groups` | Exact-duplicate group count |
| `near-matches` | Near-match count |
| `abstract-candidates` | Abstraction-candidate count |

The underlying CLI contract is:

```text
pydry check [ROOT] --config PATH --github --output PATH
```

Exit status `0` means the policy passed, `1` means findings violated the configured policy, and `2` means configuration or execution failed. The action preserves that status, so both violations and operational errors fail the step. If configuration or execution fails before analysis produces a report, the outputs may be empty. A policy failure can be inspected by later steps by assigning an `id` and using `continue-on-error: true`, but do not use that setting on the required check unless a later step deliberately restores the failure:

```yaml
- name: Run pydry for inspection
  id: pydry
  continue-on-error: true
  uses: hesreallyhim/pydry@<40-character-release-commit-sha>

- name: Show pydry result
  if: always()
  env:
    RESULT: ${{ steps.pydry.outputs.result }}
    REPORT: ${{ steps.pydry.outputs.report }}
  run: printf 'result=%s report=%s\n' "$RESULT" "$REPORT"
```

The JSON report is left in the workspace at the reported path, ready for a later artifact-upload or analysis step.
