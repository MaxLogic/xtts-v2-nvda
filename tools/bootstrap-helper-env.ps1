param(
    [string]$PythonVersion = "auto",
    [string]$Provider = "auto",
    [string]$VenvPath = ".helper-venv",
    [switch]$Recreate
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$canonicalScript = Join-Path $repoRoot "addon\bootstrap-helper-env.ps1"

if (-not (Test-Path $canonicalScript)) {
    throw "Canonical bootstrap script not found: $canonicalScript"
}

$arguments = @(
    "-PythonVersion", $PythonVersion,
    "-Provider", $Provider,
    "-VenvPath", $VenvPath
)
if ($Recreate) {
    $arguments += "-Recreate"
}

& $canonicalScript @arguments
