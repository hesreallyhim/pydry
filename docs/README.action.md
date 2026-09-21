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
        uses: hesreallyhim/pydry@v0
```

## Configure policy

Project-wide settings live as top-level keys in a standalone `pydry.toml`:

```toml
root = "src"
profile = "balanced"
exclude = ["tests", "**/generated_*.py"]
baseline = ".pydry-baseline.json"

threshold = 0.8
min_statements = 2
block_min_statements = 6
ignore_trivial = true

max_exact_groups = 0
max_block_clones = 0
max_near_matches = "none"
max_abstract_candidates = "none"
fail_on_scan_errors = true
fail_on_plugin_errors = true
annotation_limit = 25
```

The `max_*` settings are policy ceilings. A count above a configured ceiling is a violation; use the string `"none"` to leave a category unenforced. `profile` sets defaults for the sensitivity keys and the ceilings, and any key you write explicitly overrides the profile. `top_k` limits the detailed rows retained in the JSON report, but policy counts always evaluate every finding. `annotation_limit` bounds the workflow commands emitted by pydry; GitHub may impose a lower display limit.

When `baseline` names an existing file, findings recorded in it are excluded from the policy counts and marked `baselined` in the report. Generate the file locally with `pydry check --update-baseline` and commit it. Because baselines are keyed on the content of the duplicated code, an accepted finding reappears once either copy is edited.

When a key is omitted, pydry uses these built-in defaults (the `balanced` profile):

| Setting | Default |
| --- | --- |
| `root` | `.` |
| `profile` | `balanced` |
| `threshold` | `0.8` |
| `top_k` | `200` |
| `min_statements` | `2` |
| `block_min_statements` | `6` |
| `ignore_trivial` | `true` |
| `exclude` | `[]` |
| `baseline` | unset |
| `top_level_only` | `false` |
| `strict` | `true` |
| `normalize_local_names` | `true` |
| `normalize_constants` | `true` |
| `max_exact_groups` | `0` |
| `max_block_clones` | `0` |
| `max_near_matches` | `"none"` |
| `max_abstract_candidates` | `"none"` |
| `fail_on_scan_errors` | `true` |
| `fail_on_plugin_errors` | `true` |
| `annotation_limit` | `10` |

The `strict` profile additionally enforces `max_abstract_candidates = 0` and lowers the block size to `5`. The `lenient` profile raises the threshold to `0.85`, `min_statements` to `4`, the block size to `8`, and leaves blocks unenforced.

## Override settings in a workflow

Every policy input is optional. A blank input defers to `pydry.toml`; a nonblank input overrides the corresponding file value for that run. This includes `root`, which is blank by default so that the repository policy can select the scanned directory. When `config` is blank, pydry automatically loads `pydry.toml` from the working directory when it exists and otherwise uses built-in defaults.

```yaml
- name: Check duplication policy
  uses: hesreallyhim/pydry@v0
  with:
    root: src
    config: config/pydry.toml
    report: reports/pydry-report.json
    python-version: "3.12"
    profile: strict
    threshold: "0.85"
    exclude: |
      tests
      **/generated_*.py
    baseline: .pydry-baseline.json
    max-exact-groups: "0"
    max-block-clones: "0"
    max-abstract-candidates: "5"
    annotation-limit: "20"
```

Boolean overrides accept `true` or `false`. Leaving them blank preserves the project configuration. `exclude` takes one glob per line.

| Input | Default | Purpose |
| --- | --- | --- |
| `root` | blank | Directory to scan; defers to `pydry.toml` and then `.` |
| `config` | blank | Path to `pydry.toml`; blank discovers it in the workspace root |
| `report` | `.pydry/pydry-report.json` | JSON report destination |
| `python-version` | `3.11` | Python runtime for the action |
| `profile` | blank | `strict`, `balanced`, or `lenient` |
| `threshold` | blank | Near-match similarity threshold |
| `top-k` | blank | Detailed rows retained in the report |
| `min-statements` | blank | Minimum statements for a function to be analyzed |
| `block-min-statements` | blank | Minimum consecutive statements for a repeated block |
| `top-level-only` | blank | Ignore nested functions and methods |
| `strict` | blank | Stop analysis on scan errors |
| `ignore-trivial` | blank | Skip stubs, accessors, and call-free boilerplate |
| `normalize-local-names` | blank | Normalize local variable names for exact matching |
| `normalize-constants` | blank | Normalize literal values for exact matching |
| `exclude` | blank | Newline-separated globs to skip, relative to root |
| `baseline` | blank | Baseline file whose recorded findings are ignored |
| `max-exact-groups` | blank | Exact-duplicate group ceiling, or `none` |
| `max-block-clones` | blank | Repeated-block ceiling, or `none` |
| `max-near-matches` | blank | Near-match ceiling, or `none` |
| `max-abstract-candidates` | blank | Abstraction-candidate ceiling, or `none` |
| `fail-on-scan-errors` | blank | Treat scan diagnostics as policy failures |
| `fail-on-plugin-errors` | blank | Treat plugin diagnostics as policy failures |
| `annotation-limit` | blank | Maximum annotations written to the job log |

Paths and automatic configuration discovery are resolved from the current working directory, which is the checked-out repository workspace in the standard action setup. Discovery does not search parent directories or the positional scan root. Use `config`/`--config` when invoking pydry from another directory. pydry creates missing parent directories for a custom report path.

## Outputs and exit status

The action exposes these string outputs:

| Output | Meaning |
| --- | --- |
| `result` | `pass` or `fail` after analysis completes |
| `report` | Path supplied through the `report` input |
| `exact-groups` | Exact-duplicate group count |
| `block-clones` | Repeated-block count |
| `near-matches` | Near-match count |
| `abstract-candidates` | Abstraction-candidate count |

Counts are totals; when a baseline is applied, the job summary also shows how many findings are new.

The underlying CLI contract is:

```text
pydry check [ROOT] [--config PATH] --github --output PATH
```

The JSON report records the effective configuration path. The GitHub job summary emits a local reproduction command containing that path and every effective policy override, so it continues to reproduce the run even if the checked-in policy later changes.

Exit status `0` means the policy passed, `1` means findings violated the configured policy, and `2` means configuration or execution failed. The action preserves that status, so both violations and operational errors fail the step. If configuration or execution fails before analysis produces a report, the outputs may be empty. A policy failure can be inspected by later steps by assigning an `id` and using `continue-on-error: true`, but do not use that setting on the required check unless a later step deliberately restores the failure:

```yaml
- name: Run pydry for inspection
  id: pydry
  continue-on-error: true
  uses: hesreallyhim/pydry@v0

- name: Show pydry result
  if: always()
  env:
    RESULT: ${{ steps.pydry.outputs.result }}
    REPORT: ${{ steps.pydry.outputs.report }}
  run: printf 'result=%s report=%s\n' "$RESULT" "$REPORT"
```

The JSON report is left in the workspace at the reported path, ready for a later artifact-upload or analysis step.
