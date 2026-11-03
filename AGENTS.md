# Repository Guidelines

## Project Structure & Module Organization

- `src/skillman/cli.py` implements argument parsing, collection, deployment reconciliation, configuration, and state recovery. `__main__.py` exposes the module entry point.
- `bin/skillman` selects Python 3.11+ and runs the local source tree without installation.
- `scripts/hook.zsh` enables the command inside the management directory and restores PATH when leaving.
- `tests/test_skillman.py` contains filesystem integration tests and hook checks.
- `skills/<name>/` contains editable skills identified by `SKILL.md`; each skill's `.skillman` file holds TOML target switches. Root `.skillman/config.toml` defines sources and targets; `.skillman/state.json` is ignored runtime state.

## Build, Test, and Development Commands

No build step or third-party dependencies are required. Run from the repository root:

```sh
./bin/skillman --help
PYTHONPATH=src python3.11 -m unittest discover -s tests -v
source "$PWD/scripts/hook.zsh"
```

These display CLI usage, run the test suite, and enable directory-scoped commands in zsh. `./bin/skillman init` creates missing management files without overwriting existing configuration. Use `collect --dry-run` or `deploy --dry-run` to preview operations; actual commands can modify configured agent directories.

## Coding Style & Naming Conventions

Use four-space Python indentation, snake_case functions and variables, and descriptive names. Follow existing standard-library patterns: `pathlib`, `argparse`, `tomllib`, and `unittest`. No formatter or linter is configured. Keep execution offline and avoid package-manager dependencies. Preserve executable permissions on `bin/skillman` and quote shell paths.

## Testing Guidelines

Name tests `test_<behavior>` in `tests/test_skillman.py`. Use temporary directories and explicit source/target configuration; never exercise mutations against real user skills. Add regression coverage for changed behavior, especially target filtering, persisted switches, link removal, conflict protection, recovery, and read-only previews. There is no numeric coverage threshold. Run the complete suite before submitting; zsh tests skip when zsh is unavailable.

## Commit & Pull Request Guidelines

This checkout has no Git metadata, so no historical commit convention is available. Use concise imperative subjects, such as `Fix scoped deployment cleanup`. PRs should explain behavior changes, include validation commands and results, and update README examples for CLI changes. Link relevant issues when available; screenshots are unnecessary for CLI-only changes.

## Filesystem Safety

Remove or replace only links matching recorded SkillMan ownership. Preserve foreign links and real directories. Keep writes atomic, operations locked, and dry runs free of configuration or filesystem changes. Explicit single-skill target deployment must persist that target's switch as `true`; scoped synchronization must preserve unrelated deployments.
