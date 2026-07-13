param(
    [switch]$Recreate
)

$ErrorActionPreference = "Stop"

if ($PSVersionTable.PSVersion.Major -lt 7) {
    Write-Error "PowerShell 7 or newer is required. Run this script with pwsh."
    exit 1
}

function Fail([string]$Message) {
    Write-Error $Message
    exit 1
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

function Get-CpythonCandidate {
    $candidates = @(
        (Get-Command python.exe -ErrorAction SilentlyContinue),
        (Get-Command py.exe -ErrorAction SilentlyContinue)
    ) | Where-Object { $_ }

    foreach ($candidate in $candidates) {
        if ($candidate.Name -eq "py.exe") {
            $path = & py -0p 2>$null | Where-Object { $_ -match "C:\\Python3(11|12|13|14)\\python\.exe" } | Select-Object -First 1
            if ($path) {
                return $path.Trim()
            }
        } elseif ($candidate.Source) {
            return $candidate.Source
        }
    }

    return $null
}

$repoRoot = Resolve-RepoRoot
Set-Location $repoRoot

$venvRoot = Join-Path $repoRoot ".venv-phase1"
$venvPython = Join-Path $venvRoot "Scripts\python.exe"
$python = Get-CpythonCandidate
if (-not $python) {
    Fail "No stable CPython interpreter was found. Install CPython 3.11 or newer and rerun bootstrap."
}

$needsRebuild = $false
if (Test-Path -LiteralPath $venvPython) {
    $probe = & $venvPython -c "import sys; print(sys.executable); print(sys.base_prefix)"
    if ($LASTEXITCODE -ne 0) {
        $needsRebuild = $true
    } else {
        $lines = $probe -split "`r?`n" | Where-Object { $_ -ne "" }
        if ($lines.Count -lt 2 -or -not (Test-Path -LiteralPath ($lines[1]))) {
            $needsRebuild = $true
        }
    }
}

if ($needsRebuild -and -not $Recreate) {
    Fail "The existing .venv-phase1 looks broken. Rerun with -Recreate after explicitly approving recreation."
}

if ($Recreate -and (Test-Path -LiteralPath $venvRoot)) {
    Remove-Item -LiteralPath $venvRoot -Recurse -Force
}

if (-not (Test-Path -LiteralPath $venvPython)) {
    & $python -m venv $venvRoot
    if ($LASTEXITCODE -ne 0) {
        Fail "Failed to create .venv-phase1 with $python"
    }
}

$pip = Join-Path $venvRoot "Scripts\python.exe"
$installArgs = @("-m", "pip", "install", "--no-build-isolation", "-e", ".")
Write-Host "Running: $venvPython $($installArgs -join ' ')"
& $venvPython @installArgs
if ($LASTEXITCODE -ne 0) {
    Fail "Editable install failed"
}

& $venvPython -m pip check
if ($LASTEXITCODE -ne 0) {
    Fail "pip check failed"
}

& $venvPython -c "import fitz; print(fitz.__version__)"
if ($LASTEXITCODE -ne 0) {
    Fail "import fitz failed after bootstrap"
}

exit 0
