$ErrorActionPreference = "Stop"

if ($PSVersionTable.PSVersion.Major -lt 7) {
    Write-Error "PowerShell 7 or newer is required. Run this script with pwsh."
    exit 1
}

function Fail([string]$Message) {
    Write-Error $Message
    exit 1
}

function Assert-Path([string]$Path, [string]$Description) {
    if (-not (Test-Path -LiteralPath $Path)) {
        Fail "$Description is missing: $Path"
    }
}

$repoRoot = $null
$current = Get-Location
while ($true) {
    if (Test-Path -LiteralPath (Join-Path $current.Path "pyproject.toml")) {
        $repoRoot = $current.Path
        break
    }
    $parent = Split-Path -Parent $current.Path
    if (-not $parent -or $parent -eq $current.Path) {
        Fail "Could not locate the repository root from $($current.Path)"
    }
    $current = Get-Item $parent
}

Assert-Path (Join-Path $repoRoot "pyproject.toml") "pyproject.toml"
Assert-Path (Join-Path $repoRoot "finance_tracker") "finance_tracker package"
Assert-Path (Join-Path $repoRoot "tests") "tests directory"

$venvPython = Join-Path $repoRoot ".venv-phase1\Scripts\python.exe"
Assert-Path $venvPython "Virtual environment Python"

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

$fitzProbe = & $venvPython -c "import fitz; print(fitz.__version__)"
$fitzOk = $LASTEXITCODE -eq 0

Write-Host "PowerShell version: $($PSVersionTable.PSVersion)"
Write-Host "Repository path: $repoRoot"
Write-Host "Python interpreter: $($infoLines[0])"
Write-Host "Python version: $($infoLines[1])"
Write-Host "sys.prefix: $($infoLines[2])"
Write-Host "sys.base_prefix: $($infoLines[3])"
Write-Host "pip: $pipVersion"
if ($fitzOk) {
    Write-Host "PyMuPDF: $fitzProbe"
} else {
    Write-Host "PyMuPDF: import fitz failed"
}

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

if (-not $fitzOk) {
    Fail "PyMuPDF import failed inside .venv-phase1. The environment is not ready for tests."
}

exit 0
