# deps: imports that nothing declares

    agent-sense deps [--only python|js] [--ignore NAME] [--declared NAME] [--no-unused]

A coding agent adds `import foo` and the code runs on its machine, so nobody notices that the
project never declared `foo`, or that `foo` does not exist. This check compares what the code
imports with what the project declares. It reads files only: nothing is installed, executed or
looked up on a package index.

## Rules

| rule | severity | meaning |
|---|---|---|
| `undeclared-import` | fail | a third-party package is imported and no dependency file that applies to the file declares it |
| `undeclared-import` | warn | the same, but in a test, eval, example or script file, in a config file (JS), only under `if TYPE_CHECKING`, or the package is in the lock file as a dependency of another package |
| `not-in-lockfile` | warn | a declared dependency is missing from the lock file next to its declaration |
| `unused-dependency` | info | a main dependency in `pyproject.toml` that nothing imports or mentions |

## How Python is decided

- Imports come from `ast`, so strings and comments never count. Relative imports, the standard
  library and `__future__` are skipped. An import inside `try: ... except ImportError` (or
  `ModuleNotFoundError`, `Exception`, a bare `except`) is optional and skipped.
- A name is local when any `.py` file or any directory holding `.py` files in the project has that
  name, at any depth. This fits layouts that put flat modules on `sys.path`.
- Declared packages are collected from `pyproject.toml` (`project.dependencies`,
  `optional-dependencies`, `dependency-groups`, `tool.uv.dev-dependencies`, Poetry, PDM and Hatch
  tables, `build-system.requires`), `requirements*.txt` and files under `requirements/`,
  `setup.cfg`, `setup.py`, `Pipfile` and `environment.yml`. A declaration applies to the files in
  its directory and below, so one service's `pyproject.toml` does not cover the next service.
- An inline script block (PEP 723) declares packages for its own file. It also covers modules
  without a block of their own in the same service (`services/<name>/` and below) or, outside a
  services layout, under the same top-level directory: a helper that entry scripts import runs in
  their environment. It does not reach another service, another top-level directory, or files at
  the project root, and a file with its own block answers only for itself. The cost: a helper
  imported by a sibling script that does not declare the package is not reported.
- `uv run --with X` and `pip install X` in shell scripts, Makefiles, Dockerfiles, git hooks and CI
  workflows declare `X` for the whole project. This is how projects without a `pyproject.toml`
  say what their tests need. It is coarse: the package counts as declared for every file.
- Import names are matched to distribution names after normalising case and `-`/`_`/`.`, through a
  table of about 80 known differences (`yaml` is PyYAML, `PIL` is Pillow, `cv2` is opencv-python
  and so on), through namespace prefixes (`google.cloud.x` is satisfied by any `google-*`
  distribution), and through a short table of packages that always come with another
  (`pydantic` and `starlette` with FastAPI).

## How JavaScript and TypeScript are decided

- Files: `.js .jsx .ts .tsx .mjs .cjs .mts .cts .vue .svelte` (not `.d.ts`). Static `import` and
  `export ... from` must start a statement; `require()`, `import()` and `require.resolve()` with a
  literal are found anywhere. Comments are removed first.
- The package is the first path segment, or `@scope/name`. Skipped: relative and absolute paths,
  Node built-ins with or without `node:`, anything with a scheme (`virtual:`, `astro:`, `bun:`,
  `npm:`, `https:`), names starting with `$`, `~`, `#` or `@/`, aliases from `paths` in
  `tsconfig.json` / `jsconfig.json` and `imports` in `package.json`, and workspace packages (the
  `name` of every `package.json` in the project).
- A file answers to every `package.json` from its directory up to the root (all four dependency
  sections), so a workspace root's dev tools are visible to the apps below it. `@types/x` counts
  for `x`. A file with no `package.json` above it is skipped and counted in the stats.
- Lock files: `package-lock.json`, `pnpm-lock.yaml`, `yarn.lock`, `bun.lock`; for Python
  `uv.lock`, `poetry.lock`, `pdm.lock`. Dependencies given as `workspace:`, `file:`, `link:` or a
  git URL are not expected in the lock file.

## Configuration (`.agent-sense.toml`)

    [deps]
    ignore = ["some_module"]          # imports or packages never to report
    declared = ["torch"]              # provided by the runtime, treat as declared
    severity = { "unused-dependency" = "off", "undeclared-import" = "warn" }

## Results

Known-answer eval (`evals/deps_eval.py`): 14 small projects, 17 planted problems, all found, no
other finding. Field run on eight real projects (Python: 368, 47, 169, 187, 35 and 10 imports;
JS/TS: 380, 106, 715 and 9 imports): 2 findings, both judged true by reading the code (a package
imported inside a function that nothing declares). An earlier version also reported `pydantic`
in a FastAPI project that lists only `fastapi`; that is the reason for the comes-with table.

## Limits

- It does not know whether a package exists. An invented name is reported because nothing declares
  it, which is the same report a forgotten real package gets.
- A declared package that does not provide the imported name passes when the names happen to
  match, and an unknown name difference (`import foo` provided by distribution `bar`) is reported
  as undeclared until it is put in `declared` or `ignore`.
- A project directory that shares its name with a third-party package hides that package.
- Bundler aliases defined only in `vite.config`, `webpack.config` and similar are not read; add
  them to `ignore`. `tsconfig` `extends` is not followed.
- Import text at the start of a line inside a template literal is read as an import.
- `unused-dependency` is deliberately timid: main `pyproject.toml` dependencies only, and only for
  packages whose import name is known.
- Python files that do not parse are counted in `stats.python_unparsed` and skipped.
