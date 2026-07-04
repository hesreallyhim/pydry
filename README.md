# pydry

`pydry` is a small Python CLI for finding exact duplicate functions and structurally similar functions in Python code.

## Features

- Finds exact duplicate functions using AST normalization.
- Ranks near matches by structural similarity and refactorability.
- Flags likely abstraction candidates and common risk signals.
- Emits text output for quick inspection and JSON output for automation.
- Runs without third-party runtime dependencies.

## Installation

```bash
python -m pip install pydry
```

For local development from a checkout:

```bash
make venv
source venv/bin/activate
make install
make check
```

## Quick start

Run a compact summary for the current directory:

```bash
pydry showcase
```

Find exact duplicates:

```bash
pydry exact ./src --normalize-local-names --normalize-constants
```

Find near matches:

```bash
pydry near ./src --threshold 0.85 --top-k 25
```

Write a full JSON report:

```bash
pydry report ./src --output reports/pydry-report.json
```

## Commands

### `pydry exact`

Find exact duplicate functions after AST normalization.

```bash
pydry exact ./src
pydry exact ./src --min-count 3
pydry exact ./src --normalize-local-names --normalize-constants
pydry exact ./src --format json
```

Useful options:

- `--min-count`: minimum group size, default `2`.
- `--top-level-only`: ignore nested functions and methods.
- `--normalize-local-names`: treat local variable renames as equivalent.
- `--normalize-constants`: treat many literal value changes as equivalent.
- `--include-canonical`: include canonical AST dumps in JSON output.
- `--strict`: fail on files that cannot be read or parsed.

### `pydry near`

Rank structurally similar function pairs.

```bash
pydry near ./src
pydry near ./src --threshold 0.85 --top-k 25
pydry near ./src --format json --output reports/near.json
```

Useful options:

- `--threshold`: similarity threshold from `0` to `1`, default `0.8`.
- `--top-k`: cap the number of returned pairs.
- `--top-level-only`: ignore nested functions and methods.
- `--strict`: fail on files that cannot be read or parsed.

### `pydry abstract`

Filter near matches to pairs that look like plausible refactor candidates.

```bash
pydry abstract ./src
pydry abstract ./src --threshold 0.86 --format json
```

### `pydry report`

Generate one JSON document with exact, near, and abstract sections.

```bash
pydry report ./src
pydry report ./src --threshold 0.82 --top-k 250 --output reports/pydry-report.json
```

### `pydry showcase` and `pydry simulate`

Run a compact terminal summary. Both commands use the same analysis pipeline. With no path, they scan the current directory.

```bash
pydry showcase
pydry showcase ./src --top-k 10 --threshold 0.8
pydry showcase ./src --format json
pydry simulate ./src
```

## JSON output

JSON-capable commands return an envelope:

```json
{
  "results": [],
  "diagnostics": {
    "scan_errors_count": 0,
    "scan_error_samples": [],
    "plugin_errors_count": 0,
    "plugin_error_samples": []
  }
}
```

Near and abstract entries include:

- `similarity_score`
- `refactorability_score`
- `pattern_labels`
- `shared_structure_summary`
- `key_differences`
- `risk_flags`
- `suggested_refactor_kind`
- `evidence`

## Python API

The CLI is the primary interface, but the core functions are importable:

```python
from pathlib import Path

from pydry.engine import exact_groups, near_matches

groups = exact_groups(
    Path("src"),
    normalize_local_names=True,
    normalize_constants=True,
)
rows = near_matches(Path("src"), threshold=0.85, top_k=25)
```

## Limitations

- Similarity is heuristic. It does not prove semantic equivalence.
- Cross-file import resolution is not attempted.
- Generated abstraction templates are suggestions, not executable patches.

## Development

```bash
make check
make coverage
make check-dist
```

The package supports Python 3.11 and newer.

## License

`pydry` is released under the MIT License. See [LICENSE](LICENSE).
