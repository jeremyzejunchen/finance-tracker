param(
    [switch]$Recreate,
    [string]$PythonPath
)

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

function Get-PythonValidationResult {
    param(
        [string]$InterpreterPath
    )

    if (-not $InterpreterPath -or -not (Test-Path -LiteralPath $InterpreterPath)) {
        return $null
    }

    try {
        $result = & $InterpreterPath -c "import platform, sys; print(platform.python_implementation()); print(sys.version_info.major); print(sys.version_info.minor); print(sys.executable)" 2>$null
    } catch {
        return $null
    }
    if ($LASTEXITCODE -ne 0) {
        return $null
    }

    $lines = @($result | ForEach-Object { $_.Trim() }) | Where-Object { $_ -ne "" }
    if ($lines.Count -lt 4) {
        return $null
    }

    if ($lines[0] -ne "CPython") {
        return $null
    }

    $major = 0
    $minor = 0
    if (-not [int]::TryParse($lines[1], [ref]$major)) {
        return $null
    }
    if (-not [int]::TryParse($lines[2], [ref]$minor)) {
        return $null
    }

    if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 11)) {
        return $null
    }

    $reportedExecutable = $lines[3]
    if (-not $reportedExecutable -or -not (Test-Path -LiteralPath $reportedExecutable)) {
        return $null
    }

    return $reportedExecutable
}

function Add-Candidate {
    param(
        [string]$CandidatePath,
        [System.Collections.Generic.List[string]]$CandidateList,
        [System.Collections.Generic.HashSet[string]]$SeenCandidates
    )

    if (-not $CandidatePath) {
        return
    }

    $normalized = $CandidatePath.Trim('"')
    if ([string]::IsNullOrWhiteSpace($normalized)) {
        return
    }

    if ($SeenCandidates.Add($normalized)) {
        $CandidateList.Add($normalized)
    }
}

function Get-CpythonCandidate {
    param(
        [string]$ExplicitPythonPath
    )

    if ($ExplicitPythonPath) {
        $validated = Get-PythonValidationResult -InterpreterPath $ExplicitPythonPath
        if (-not $validated) {
            Fail "The explicitly supplied PythonPath is not a valid CPython 3.11 or newer interpreter: $ExplicitPythonPath"
        }

        return $validated
    }

    $seenCandidates = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::OrdinalIgnoreCase)
    $candidateList = New-Object 'System.Collections.Generic.List[string]'

    $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($pythonCommand -and $pythonCommand.Source) {
        Add-Candidate -CandidatePath $pythonCommand.Source -CandidateList $candidateList -SeenCandidates $seenCandidates
    }

    $pyCommand = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($pyCommand) {
        try {
            $launcherOutput = & $pyCommand.Source -0p 2>$null
            if ($LASTEXITCODE -eq 0) {
                foreach ($line in $launcherOutput) {
                    if ($line -match '([A-Za-z]:\\.*\\python(?:w)?\.exe)\s*$') {
                        Add-Candidate -CandidatePath $Matches[1] -CandidateList $candidateList -SeenCandidates $seenCandidates
                    }
                }
            }
        } catch {
        }
    }

    foreach ($candidatePath in $candidateList) {
        $validated = Get-PythonValidationResult -InterpreterPath $candidatePath
        if ($validated) {
            return $validated
        }
    }

    return $null
}

$repoRoot = Resolve-RepoRoot
Set-Location $repoRoot

$venvRoot = Join-Path $repoRoot ".venv-phase1"
$venvPython = Join-Path $venvRoot "Scripts\python.exe"

$venvExists = Test-Path -LiteralPath $venvRoot
$venvPythonExists = Test-Path -LiteralPath $venvPython
$needsRebuild = $venvExists -and -not $venvPythonExists

if ($venvPythonExists) {
    try {
        $probe = & $venvPython -c "import sys; print(sys.executable); print(sys.base_prefix)" 2>$null
        if ($LASTEXITCODE -ne 0) {
            $needsRebuild = $true
        } else {
            $lines = $probe -split "`r?`n" | Where-Object { $_ -ne "" }
            if ($lines.Count -lt 2 -or -not (Test-Path -LiteralPath ($lines[1]))) {
                $needsRebuild = $true
            }
        }
    } catch {
        $needsRebuild = $true
    }
}

if ($needsRebuild -and -not $Recreate) {
    Fail "The existing .venv-phase1 looks broken. Rerun with -Recreate after explicitly approving recreation."
}

if (-not $venvPythonExists -or $needsRebuild -or $Recreate) {
    if ($Recreate -and $venvExists) {
        Remove-Item -LiteralPath $venvRoot -Recurse -Force
    }

    $python = Get-CpythonCandidate -ExplicitPythonPath $PythonPath
    if (-not $python) {
        Fail "No stable CPython interpreter was found. Install CPython 3.11 or newer and rerun bootstrap."
    }

    & $python -m venv $venvRoot
    if ($LASTEXITCODE -ne 0) {
        Fail "Failed to create .venv-phase1 with $python"
    }

    if (-not (Test-Path -LiteralPath $venvPython)) {
        Fail "Failed to create .venv-phase1\Scripts\python.exe"
    }
}

$installPython = $venvPython
$installArgs = @("-m", "pip", "install", "-e", ".")
Write-Host "Running: $installPython $($installArgs -join ' ')"
& $installPython @installArgs
if ($LASTEXITCODE -ne 0) {
    Fail "Editable install failed"
}

& $installPython -m pip check
if ($LASTEXITCODE -ne 0) {
    Fail "pip check failed"
}

exit 0
