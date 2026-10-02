# Quality baselines

Install the pinned tools and NumPy stubs in a project virtual environment:

```sh
python -m venv .venv
# Activate .venv using your shell, then:
python -m pip install -c requirements-ci.txt -e ".[dev]"
```

Run the same checks as CI:

```sh
python scripts/check_baselines.py check all
python scripts/check_baselines.py fresh all
python -m mypy --python-version 3.11 src/arty_trading/modules/smc/
python -m pytest
```

Ruff has no native `baseline` configuration option. Its raw `ruff check .`
command continues to return 1 for existing diagnostics. The versioned filter
consumes `ruff check . --output-format=json` and compares each diagnostic's
relative path, rule, message and source lines against `.ruff-baseline.json`.
Line-number shifts are tolerated; additional identical diagnostics are counted
and rejected. No rules or files are globally ignored to hide the baseline.

The mypy filter uses `mypy-baseline` and `mypy-baseline.txt`. Its ordinary
`filter` tolerates resolved entries, but never new ones. CI checks the whole
`src` tree explicitly, with strict mode and Python 3.11. `follow_imports =
"silent"` keeps a targeted SMC check from reporting unrelated imported modules;
it still analyzes their types and does not exclude them from the global check.

After fixing existing errors, update and commit the shrinking baselines:

```sh
python scripts/check_baselines.py sync all
```

Synchronization refuses new errors. Initial baselines are created only when
the files are absent; do not delete them to accept a regression. The freshness
job regenerates both files and checks their Git diff, requiring obsolete
entries to be removed. Tool crashes, configuration errors and incomplete mypy
checks fail rather than being interpreted as clean results.

NumPy is constrained below 2.3, and CI pins 2.2.6 to provide stubs parseable for
the Python 3.11 target, including when mypy itself runs under Python 3.13.
Tool versions are pinned because rule changes can invalidate a baseline. Update
these pins and baselines together after reviewing changes. CI runs on Windows
because the project's MetaTrader5 dependency supplies Windows wheels.
