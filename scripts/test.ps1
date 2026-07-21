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

$repoRoot = Resolve-RepoRoot
Set-Location $repoRoot

$venvPython = Join-Path $repoRoot ".venv-phase1\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython)) {
    Fail ".venv-phase1 is missing. Run scripts/bootstrap.ps1 first."
}

$tempRoot = Join-Path $repoRoot ".tmp"
$testTemp = Join-Path $tempRoot "tests"
$localAppData = Join-Path $tempRoot "localappdata"
New-Item -ItemType Directory -Force -Path $testTemp | Out-Null
New-Item -ItemType Directory -Force -Path $localAppData | Out-Null

$env:TEMP = $testTemp
$env:TMP = $testTemp
$env:LOCALAPPDATA = $localAppData
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

& $venvPython -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

node .\tests\test_ui.mjs
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

node .\tests\test_kontoumsaetze_browser.mjs
exit $LASTEXITCODE
