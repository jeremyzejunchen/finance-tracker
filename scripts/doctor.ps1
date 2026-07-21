$ErrorActionPreference = "Stop"

if ($PSVersionTable.PSVersion.Major -lt 7) {
    Write-Error "PowerShell 7 or newer is required. Run this script with pwsh."
    exit 1
}

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

function Fail([string]$Message) {
    Write-Error $Message
    exit 1
}

function Assert-Path([string]$Path, [string]$Description) {
    if (-not (Test-Path -LiteralPath $Path)) {
        Fail "$Description is missing: $Path"
    }
}

function Resolve-RepoRoot {
    $currentPath = (Get-Location).Path

    while ($true) {
        if (Test-Path -LiteralPath (Join-Path $currentPath "pyproject.toml")) {
            return $currentPath
        }

        $parentPath = Split-Path -Parent $currentPath
        if (-not $parentPath -or $parentPath -eq $currentPath) {
            Fail "Could not locate the repository root from $currentPath"
        }

        $currentPath = $parentPath
    }
}

$repoRoot = Resolve-RepoRoot

Assert-Path (Join-Path $repoRoot "pyproject.toml") "pyproject.toml"
Assert-Path (Join-Path $repoRoot "finance_tracker") "finance_tracker package"
Assert-Path (Join-Path $repoRoot "tests") "tests directory"

$venvPython = Join-Path $repoRoot ".venv-phase1\Scripts\python.exe"
Assert-Path $venvPython "Virtual environment Python"

$unicodeSentinel = & $venvPython -c "import sys; sys.stdout.write('\u9879\u76ee')"
if ($LASTEXITCODE -ne 0) {
    Fail "Unable to verify Python UTF-8 output"
}

$expectedUnicodeSentinel = [string]::Concat([char]0x9879, [char]0x76EE)
if ($unicodeSentinel -cne $expectedUnicodeSentinel) {
    Fail "Python UTF-8 output check failed"
}

$pythonInfo = & $venvPython -c "import sys; print(sys.executable); print(sys.version); print(sys.prefix); print(sys.base_prefix)"
if ($LASTEXITCODE -ne 0) {
    Fail "Unable to execute .venv-phase1\Scripts\python.exe"
}

$infoLines = $pythonInfo -split "`r?`n" | Where-Object { $_ -ne "" }
if ($infoLines.Count -lt 4) {
    Fail "Unexpected Python probe output: $pythonInfo"
}

$pipVersion = & $venvPython -m pip --version
if ($LASTEXITCODE -ne 0) {
    Fail "Unable to query pip version from the virtual environment"
}

Write-Host "PowerShell version: $($PSVersionTable.PSVersion)"
Write-Host "Repository path: $repoRoot"
Write-Host "Python interpreter: $($infoLines[0])"
Write-Host "Python version: $($infoLines[1])"
Write-Host "sys.prefix: $($infoLines[2])"
Write-Host "sys.base_prefix: $($infoLines[3])"
Write-Host "pip: $pipVersion"

$pyvenvCfg = Join-Path $repoRoot ".venv-phase1\pyvenv.cfg"
if (Test-Path -LiteralPath $pyvenvCfg) {
    $cfg = Get-Content -LiteralPath $pyvenvCfg
    $homeLine = $cfg | Where-Object { $_ -like "home = *" } | Select-Object -First 1
    $executableLine = $cfg | Where-Object { $_ -like "executable = *" } | Select-Object -First 1
    if ($homeLine) {
        Write-Host $homeLine
    }
    if ($executableLine) {
        Write-Host $executableLine
    }
    if ($executableLine -and -not (Test-Path ($executableLine -replace "^executable = ", ""))) {
        Fail "The virtual environment points to a missing base interpreter: $executableLine"
    }
}

exit 0
