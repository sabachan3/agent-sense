# Contracts

Every check is a service under `services/<name>/` with one module `<name>_lib.py`. The command line
talks to a service only through the four names below, and services never import each other.

## Service interface

```python
NAME = "deps"                       # the subcommand: agent-sense deps
SUMMARY = "one line for --help"
RULES = {"undeclared-import": "fail", ...}   # every rule the service can emit -> default severity

def add_arguments(parser): ...      # add the service's own options to an argparse parser
def run(args) -> dict: ...          # {"findings": [finding, ...], "stats": {...}}
```

`run` receives the parsed arguments. `args.root` is always present: the project directory to
check (absolute `pathlib.Path`). `args.config` is the service's table from `.agent-sense.toml`
(a dict, empty when there is none). A service must not print, must not call `sys.exit`, and
raises `RuntimeError("message")` when it cannot run at all (the command line turns that into
exit code 2).

## Finding

| field | type | meaning |
|---|---|---|
| `rule` | text | one of the service's `RULES` |
| `severity` | `fail`, `warn` or `info` | `fail` stops a commit or a hand-off; `info` never changes the exit code |
| `message` | text | what is wrong, in one sentence, with the concrete names and numbers |
| `fix` | text | what to do about it (may be empty) |
| `file` | text, optional | path relative to `args.root` |
| `line` | number, optional | 1-based |
| `detail` | object, optional | anything else worth keeping in the JSON report |

`detail.lines`, when present, is a list of text lines (for example the tail of a failing test run) that the
text report prints under the finding.

Text that comes from a checked project or page (file names, page text, selectors) is data: put it
in the fields as it is, and the command line strips control characters before printing.

## Report (version 1)

`{"version": 1, "check": NAME, "verdict": "fail"|"warn"|"ok", "summary": {"fail": n, "warn": n, "info": n}, "stats": {...}, "findings": [...]}`

Exit codes: 0 nothing at or above `--fail-on` (default `fail`), 1 otherwise, 2 the tool could not run.
