# Noise benchmark

Defaults are tuned against packages from the Python standard library. That code is well maintained and has been reviewed for years, so most whole-function similarity in it is idiomatic (visitor methods, protocol stubs, accessor families) rather than a copy someone forgot to consolidate. A setting that produces hundreds of findings there is producing noise.

Reproduce with:

```bash
make benchmark
python benchmarks/stdlib_noise.py --packages email asyncio --show 5
python benchmarks/stdlib_noise.py --threshold 0.7 --block-min-statements 8
```

## Current defaults

Threshold `0.8`, minimum `2` statements, trivial bodies skipped, block size `6`. Measured on CPython 3.11.

| Package | Functions | Analyzed | Time | Exact groups | Repeated blocks | Near pairs |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| email | 530 | 351 | 1.4 s | 9 | 19 | 11 |
| asyncio | 983 | 617 | 2.0 s | 7 | 23 | 39 |
| json | 31 | 27 | 0.2 s | 0 | 5 | 1 |
| http | 230 | 183 | 0.7 s | 2 | 4 | 5 |
| unittest | 2569 | 1599 | 6.0 s | 58 | 60 | 238 |
| logging | 258 | 191 | 0.6 s | 1 | 4 | 14 |

"Analyzed" is the number of functions left after the minimum-statement and triviality filters. The `unittest` package includes its own test suite, which is repetitive by design; a project would normally exclude tests or use the `lenient` profile for them.

## Before the statement-alignment engine

The previous scoring combined a bag of node types (40%), a longest common subsequence over statement kinds (20%), call-name overlap (15%), signature similarity (10%), and wrapper and currying heuristics (15%). It had no minimum size and did not skip trivial bodies. On the same packages at the same threshold:

| Package | Near pairs | Pairs where every core metric scored 1.0 | Pairs whose first side is 3 lines or fewer |
| --- | ---: | ---: | ---: |
| email | 218 | 188 | 171 |
| asyncio | 991 | 953 | 700 |

The top-ranked asyncio pair was `write_eof` against `__del__`, both one-liners. The top-ranked pair longer than eight lines was a docstring followed by `raise NotImplementedError`. Meanwhile a genuine variant (two loaders of about fifteen statements differing by one inserted guard) scored 0.71 and was not reported at all.

## What the top findings look like now

From the same runs, the first results per package:

- email: `get_attribute` and `get_extended_attribute` share 11 of 12 statements with one parameter hard-coded; the `get_atext`, `get_ttext`, `get_attrtext` family differs only in constants.
- asyncio: `_sock_recv`, `_sock_recv_into`, and `_sock_recvfrom` share an 8-statement block five times; `subprocess_exec` and `subprocess_shell` share 18 statements.
- json: `_iterencode_dict` and `_iterencode` share a 19-statement block.
- http: `HTTPResponse.read`, `read1`, and `readline` share an 8-statement block; `set_ok_verifiability` and `return_ok_verifiability` are identical.
- logging: the `Logger.debug`, `info`, `warning`, `error` family differs only in the level constant.

These are the kinds of finding a reviewer can act on or consciously accept. Whether to act is a project decision, which is why the check command has profiles, exclusions, and a baseline.
