$ErrorActionPreference = "Stop"

function Fail([string]$Message) {
    Write-Error $Message
    exit 1
}

function Resolve-RepoRoot {
    $current = Get-Location
    while ($true) {
        if (Test-Path -LiteralPath (Join-Path $current.Path "pyproject.toml")) {
            return $current.Path
        }
        $parent = Split-Path -Parent $current.Path
        if (-not $parent -or $parent -eq $current.Path) {
            Fail "Could not locate the repository root from $($current.Path)"
        }
        $current = Get-Item $parent
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
exit $LASTEXITCODE
