# boundary: services that reach into each other

    agent-sense boundary [--services-dir DIR] [--allow NAME | --allow "a->b"]

The layout is one directory per concern under `services/<name>/`, talking through shared
contracts. That is what lets two agent sessions work on two services at the same time. An agent
that needs a helper from the next service imports it, the tests pass, and the two services can no
longer change separately. This check finds those reaches.

## Why not import-linter or dependency-cruiser

Both describe rules between importable packages. In this layout a service is a folder of flat
modules that a top-level script puts on `sys.path` (`import score_lib`, not
`from services.score import score_lib`), so there is no package graph for them to read. The check
here works from the files: which service's folder defines which module name.

## Rules

| rule | severity | meaning |
|---|---|---|
| `cross-service-import` | fail | a file in `services/a/` imports a module that only another service defines, imports `services.b...`, uses a relative import that leaves its service, or (JS/TS) imports a path that resolves into another service |
| `cross-service-syspath` | fail | `sys.path.insert/append/extend` with a path into another service |
| `cross-service-path` | warn | a string or `pathlib` expression in a `.py` or shell file that names a path inside another service (`services/b/...`, `../b/...`, `HERE.parent / "b"`) |

Details:

- A module belongs to a service when it is a `.py` file directly in the service folder or a
  subfolder of it holding Python (not `tests`). Tests are checked like any other file.
- A module name that the importing service also defines is its own (`lib.py` in every service is
  fine). A name that is also a standard-library module is never reported.
- Allowed targets: `contracts` and `schemas`, plus `allow` entries. An entry is a service name
  (anything may use it), or `a->b` for one direction between two services.
- Docstrings and comments are prose and are not checked.
- Code outside the services directory is glue and may import any service.

A service that runs another service's entry point as a subprocess is reported by
`cross-service-path`. That is a reach by path, and it is often the intended contract (JSON in,
JSON out). When it is, record it: `allow = ["score->llm"]`. The warning then documents every such
edge in one place instead of forbidding them.

## Configuration (`.agent-sense.toml`)

    [boundary]
    services-dir = "services"
    allow = ["llm", "review->score"]
    severity = { "cross-service-path" = "fail" }

## Results

Known-answer eval (`evals/boundary_eval.py`): 11 projects, 10 planted reaches, all found, no other
finding. Field run on four real projects (14, 4, 10 and 7 services; 117 files): no import or
sys.path reach, and 4 `cross-service-path` warnings. All four were read: each is a service calling
the LLM service's command-line entry point as a subprocess, intended in those projects, and each
goes away with one `allow` line.

## Limits

- Imports built from strings (`__import__(name)`, `importlib.import_module(variable)`) are not seen.
- A workspace package of another service imported by its package name (JS) is treated as that
  service's public interface and not reported.
- A third-party package with the same name as another service's module is reported as a reach.
- `cross-service-path` reads literals only; a path assembled from variables is not seen.
- Only `.py`, JS/TS and shell files inside the services directory are read.
