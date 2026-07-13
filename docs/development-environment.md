# Development Environment

This repository targets native Windows with PowerShell 7 and a single project virtual environment at `.venv-phase1`.

## Prerequisites

- Windows 11
- PowerShell 7 available as `pwsh`
- CPython 3.11 or newer
- Git

## Canonical setup

- Use `pwsh -NoProfile -File` to run repository scripts.
- Use only `.venv-phase1\Scripts\python.exe` for testing.
- Do not use system `python`, `py`, uv-managed Python, or any other virtual environment for normal development.

## First bootstrap

Run:

```powershell
pwsh -NoProfile -File .\scripts\bootstrap.ps1
```

The bootstrap script:

- checks whether `.venv-phase1` already exists
- stops if the environment looks broken unless `-Recreate` is passed
- creates the environment with a stable CPython interpreter
- installs the project in editable mode using `.venv-phase1\Scripts\python.exe -m pip`
- runs `pip check`
- verifies `import fitz`

If the environment is corrupted, run:

```powershell
pwsh -NoProfile -File .\scripts\bootstrap.ps1 -Recreate
```

Do not manually edit `pyvenv.cfg` or patch files inside `.venv-phase1`.

## Environment check

Run:

```powershell
pwsh -NoProfile -File .\scripts\doctor.ps1
```

This command reports:

- PowerShell version
- repository path
- selected Python interpreter
- Python version
- `sys.prefix`
- `sys.base_prefix`
- `pip` version
- `PyMuPDF` availability

If `import fitz` fails, the environment is not ready for tests.

## Normal tests

Run:

```powershell
pwsh -NoProfile -File .\scripts\test.ps1
```

This script:

- uses only `.venv-phase1\Scripts\python.exe`
- creates `.tmp\tests` and `.tmp\localappdata` inside the repository
- sets `TEMP`, `TMP`, `LOCALAPPDATA`, `PYTHONUTF8`, and `PYTHONIOENCODING`
- runs `python -m unittest discover -s tests -v`

Normal tests do not install dependencies and do not need network access.

## Broken virtual environment

Treat the environment as broken if any of the following is true:

- `.venv-phase1\Scripts\python.exe` is missing
- the interpreter path in `.venv-phase1\pyvenv.cfg` points to a deleted base Python
- `doctor.ps1` cannot run `import fitz`
- `pip check` fails after bootstrap

If the environment is broken, recreate it instead of editing internal files.

## Temporary paths

Test writes must stay inside the repository:

- `.tmp\tests`
- `.tmp\localappdata`

Do not write test data to the real `%LOCALAPPDATA%\FinanceTracker`, the user profile, or the global Python installation.

## Common failures

- `environment`: broken venv, missing Python, missing `fitz`, wrong interpreter
- `sandbox`: blocked filesystem or command execution
- `permission`: denied write or path access
- `network`: attempted dependency download or remote access
- `dependency`: install or package resolution problem
- `test`: assertion failure
- `application logic`: parser, service, database, or UI behavior regression

## WSL2 fallback

WSL2 is a fallback, not the canonical environment.

- Use it only if the native Windows sandbox remains unreliable after this work, or if Linux-native tooling becomes necessary.
- A WSL environment needs its own Linux virtual environment.
- The Windows `.venv-phase1` cannot be reused inside WSL.
- A future WSL clone should live under `~/code/finance-tracker`, not under `/mnt/c` or `/mnt/d`.
- Windows and WSL must never share the same virtual environment.
- `%LOCALAPPDATA%` is Windows-specific and must be handled deliberately in WSL.

## Why bootstrap and tests are separate

Bootstrap is the only place where environment repair or dependency installation should happen. Normal tests assume the environment already exists and should not touch the network.

