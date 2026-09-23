# pydry

pydry finds duplicated Python code and ranks it by how much you would save by consolidating it. It works on the syntax tree, so renamed variables, changed literals, and reformatted code do not hide a copy.

It reports three kinds of finding:

- **Exact duplicates**: whole functions that are equivalent after normalization, labeled with the tightest equivalence that holds (`identical`, `renamed`, or `constants`).
- **Repeated blocks**: runs of consecutive statements copied into two or more places, including the middle of larger functions.
- **Near matches**: pairs of functions that share most of their statements, with a diff-derived explanation of what differs (inserted statements, changed constants, a parameter hard-coded on one side) and a suggested refactor.

Findings are ranked by estimated savings in statements, weighted by a confidence score, so the first result is the one most worth acting on. Stubs, accessors, and other call-free boilerplate are skipped by default.

## Installation

```bash
python -m pip install pydry-cli
```

For local development from a checkout:

```bash
make venv
source venv/bin/activate
make install
make check
```

## Quick start

```bash
pydry showcase ./src
```

```text
Summary: exact_groups=6 repeated_blocks=1 near_pairs=3 abstract_candidates=3

[1/3] Exact duplicates (whole functions)
  1. 3x renamed, 8 statements, saves ~16: process_csv_row, process_json_entry, clean_record
  2. 2x constants, 5 statements, saves ~5: build_user_query, build_admin_query

[2/3] Repeated blocks (inside larger functions)
  1. 11 statements x2, saves ~11: parse_records:12-22, summarize_file:27-37

[3/3] Near matches (ranked by priority)
  1. priority 7.6, 8 shared statements, saves ~8 (delegate_to_general_form): retry, retry_quickly
  2. priority 4.8, 5 shared statements, saves ~5 (delegate_to_general_form): clamp_value, clamp_to_unit
```

Each command also has a `--format json` mode for tooling.

## Commands

### `pydry exact`

Whole functions that are duplicates under normalization. Local variable names and literal values are normalized by default; docstrings, annotations, decorators, and parameter names are always ignored.

```bash
pydry exact ./src
pydry exact ./src --no-normalize-constants     # only report renamed or identical copies
pydry exact ./src --min-count 3 --format json
```

Each group reports its `tier`, its statement count, and `savings`, the number of statements that would disappear if all copies but one were removed.

### `pydry blocks`

Runs of statements repeated in two or more places. This is what catches a parsing loop pasted into the middle of a larger function, which whole-function comparison cannot see.

```bash
pydry blocks ./src
pydry blocks ./src --block-min-statements 8    # only longer runs
```

Blocks are matched on the normalized statement form, so renamed locals and changed constants still match. Runs that cover most of both functions are left to the exact and near-match reports.

### `pydry near`

Pairs of functions that share at least `--threshold` of their statements, measured as `2 * shared / (len_a + len_b)` over an alignment of the two statement sequences.

```bash
pydry near ./src
pydry near ./src --threshold 0.7 --top-k 20
```

Results are grouped into clusters of transitively connected functions. Each pair reports:

- `shared_statements` and the length of each side, so you can see the diff at a glance.
- `pattern_labels` derived from the alignment: `renamed_locals`, `literal_specialization`, `parameter_specialization`, `structural_variant`, `mixed_variation`, `different_dependencies`.
- `risk_flags` that discourage a merge: `async_boundary_diff`, `return_shape_diff`, `exception_behavior_diff`, `possible_side_effects`, `ambient_dependency_diff`.
- `suggested_refactor_kind`: `remove_duplicate`, `parameterize_constant`, `delegate_to_general_form`, `extract_common_helper`, `extract_shared_steps`, `inject_dependency`, or `leave_separate`.
- `refactorability_score`, a confidence estimate, and `priority`, which is shared statements multiplied by that confidence.

Functions that are exact duplicates of one another are represented by a single member, so exact groups are never repeated here.

### `pydry abstract`

The near matches whose suggestion is not `leave_separate`.

### `pydry report`

One JSON document with `exact`, `blocks`, `near`, and `abstract` sections plus a summary.

```bash
pydry report ./src --output reports/pydry-report.json
```

### `pydry check`

Evaluate findings against a policy and return a CI-friendly status: `0` for pass, `1` for a policy violation, or `2` for a configuration or execution failure.

```bash
pydry check
pydry check ./src --profile strict
pydry check --baseline .pydry-baseline.json
pydry check --update-baseline
```

Policy lives in a `pydry.toml` next to your code:

```toml
root = "src"
profile = "balanced"        # strict | balanced | lenient
exclude = ["tests", "**/generated_*.py"]
baseline = ".pydry-baseline.json"

max_exact_groups = 0        # fail on any whole-function duplicate
max_block_clones = 0        # fail on any repeated block
max_abstract_candidates = "none"   # report near matches, do not enforce
```

With a baseline, `check` only counts findings that are not already recorded, so an existing codebase can adopt pydry without first paying down every duplicate. Run `pydry check --update-baseline` to accept the current state, commit the file, and the check will fail only on new duplication. Baselines are keyed on the normalized content of the duplicated code and record how many copies were accepted. An accepted finding resurfaces when another copy appears or when a copy changes structurally; renaming locals or changing literal values in an accepted copy keeps it accepted, because those differences are normalized away.

Profiles set defaults for the sensitivity knobs; explicit keys override them:

| Profile | Threshold | Min statements | Block size | Enforced by default |
| --- | --- | --- | --- | --- |
| `strict` | 0.8 | 2 | 5 | exact, blocks, abstract |
| `balanced` | 0.8 | 2 | 6 | exact, blocks |
| `lenient` | 0.85 | 4 | 8 | exact |

Not every project weights DRYness the same way. Test suites in particular are repetitive by design, which is what `exclude` and `lenient` are for.

### Shared options

Every analysis command accepts:

- `--min-statements N`: ignore functions with fewer statements in every analysis, including block detection (default `2`).
- `--no-ignore-trivial`: also analyze stubs, accessors, and call-free boilerplate.
- `--exclude GLOB`: skip paths matching a glob relative to the root; repeatable.
- `--top-level-only`: ignore nested functions and methods.
- `--strict`: fail on files that cannot be read or parsed.

## GitHub Actions

The repository ships as a composite action that runs `pydry check`, writes the JSON report, and adds findings as annotations:

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
      - uses: actions/checkout@v7
      - uses: hesreallyhim/pydry@v0
```

Action inputs mirror the `pydry.toml` keys and override them individually. See the [integration guide](docs/README.action.md).

## How it works

Every function is flattened into a sequence of statement tokens. Each token is the statement's syntax tree with local names replaced by positional placeholders and constants replaced by typed placeholders, so `total += item.price` and `acc += row.price` produce the same token while `acc += row.cost` does not. Attribute names, module-level names, and imports are kept, because they are what the code depends on.

- Exact groups hash the whole normalized function at three tiers and report the tightest one that holds.
- Near matches align two token sequences with a longest common subsequence. The aligned statements are compared again at the stricter tiers to classify what differs, and a looser tier that also equates parameters with literals catches a function that hard-codes an argument of its sibling.
- Repeated blocks hash every window of consecutive tokens, extend matching windows to maximal runs, and group runs by content.

The scores are heuristics: a high similarity means two functions look alike structurally, not that they are semantically interchangeable. The risk flags and the `leave_separate` suggestion exist because structurally similar code is sometimes best left apart.

## Noise on real code

Defaults are tuned against the standard library, which is well maintained and where most whole-function similarity is idiomatic rather than a copy. The benchmark script and its current numbers are in [docs/benchmarks.md](docs/benchmarks.md); run `make benchmark` to reproduce.

## Migrating from 0.0.x

The 0.1 engine changes defaults and the JSON schema:

- `pydry exact` now normalizes local names and constants by default. Pass `--no-normalize-local-names` or `--no-normalize-constants` for the old behavior, and the same keyword arguments to `exact_groups()`.
- Functions with fewer than two statements and trivial bodies (stubs, accessors, call-free boilerplate) are skipped by default. Use `--min-statements 1 --no-ignore-trivial` to include them.
- Near-match evidence is now alignment counts (`shared_statements`, `only_in_a`, `constant_statements`, and so on). The `shape_similarity`, `stmt_similarity`, `signature_similarity`, `wrapper_score`, and `curry_score` fields, the `wrapper` and `partial_application` labels, and `abstract_template` are gone. Results gain `priority`, `shared_statements`, and `cluster_id`.
- Reports and `check` output gain a `blocks` section, and the check policy gains `max_block_clones`, which defaults to `0`. `max_abstract_candidates` now defaults to unenforced; set `profile = "strict"` to restore the old ceiling of `0`.

## Python API

The core functions are importable:

```python
from pathlib import Path

from pydry.engine import block_clones, exact_groups, near_matches, scan_functions

profiles = scan_functions(Path("src"))
groups = exact_groups(Path("src"), profiles=profiles)
pairs = near_matches(Path("src"), threshold=0.8, profiles=profiles)
blocks = block_clones(Path("src"), profiles=profiles)
```

Passing `profiles` lets several analyses share one scan.

## License

MIT. See [LICENSE](LICENSE).
