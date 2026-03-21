param(
    [string]$TargetRoot = "$env:APPDATA\nvda\addons\maxlogicXTTSv2",
    [switch]$Force
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$sourceAddonRoot = Join-Path $repoRoot "addon"
$sourceGlobalPlugins = Join-Path $sourceAddonRoot "globalPlugins"
$sourceSynthDrivers = Join-Path $sourceAddonRoot "synthDrivers"
$sourceDoc = Join-Path $sourceAddonRoot "doc"
$sourceInstallTasks = Join-Path $sourceAddonRoot "installTasks.py"
$sourceBootstrapScript = Join-Path $sourceAddonRoot "bootstrap-helper-env.ps1"
$sourceHelperVenv = Join-Path $repoRoot ".helper-venv"
$sourceTools = Join-Path $repoRoot "tools"

if (Test-Path $TargetRoot) {
    if (-not $Force) {
        throw "Target already exists: $TargetRoot . Re-run with -Force to replace it."
    }
    Remove-Item $TargetRoot -Recurse -Force
}

New-Item -ItemType Directory -Path $TargetRoot | Out-Null

$manifest = @"
name = maxlogicXTTSv2
summary = "MaxLogic XTTS v2"
description = """An XTTS v2 speech synthesizer add-on for NVDA with profile management, previews, and speech caching."""
author = "MaxLogic"
url = None
version = 0.1.0
docFileName = readme.html
minimumNVDAVersion = 2024.1
lastTestedNVDAVersion = 2025.1
updateChannel = None
"@

Set-Content -Path (Join-Path $TargetRoot "manifest.ini") -Value $manifest -Encoding UTF8
Copy-Item $sourceInstallTasks (Join-Path $TargetRoot "installTasks.py") -Force
Copy-Item $sourceBootstrapScript (Join-Path $TargetRoot "bootstrap-helper-env.ps1") -Force

New-Item -ItemType Junction -Path (Join-Path $TargetRoot "synthDrivers") -Target $sourceSynthDrivers | Out-Null
if (Test-Path $sourceGlobalPlugins) {
    New-Item -ItemType Junction -Path (Join-Path $TargetRoot "globalPlugins") -Target $sourceGlobalPlugins | Out-Null
}
New-Item -ItemType Junction -Path (Join-Path $TargetRoot "doc") -Target $sourceDoc | Out-Null
if (Test-Path $sourceTools) {
    New-Item -ItemType Junction -Path (Join-Path $TargetRoot "tools") -Target $sourceTools | Out-Null
}

if (Test-Path $sourceHelperVenv) {
    New-Item -ItemType Junction -Path (Join-Path $TargetRoot ".helper-venv") -Target $sourceHelperVenv | Out-Null
}

Write-Host "Development install created at $TargetRoot"
Write-Host "synthDrivers -> $sourceSynthDrivers"
if (Test-Path $sourceGlobalPlugins) {
    Write-Host "globalPlugins -> $sourceGlobalPlugins"
}
Write-Host "doc -> $sourceDoc"
if (Test-Path $sourceTools) {
    Write-Host "tools -> $sourceTools"
}
if (Test-Path $sourceHelperVenv) {
    Write-Host ".helper-venv -> $sourceHelperVenv"
}
