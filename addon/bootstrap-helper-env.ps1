param(
    [ValidateSet("auto", "cuda", "cpu")]
    [string]$Provider = "auto",
    [string]$PythonVersion = "auto",
    [string]$VenvPath = ".helper-venv",
    [switch]$Recreate
)

$ErrorActionPreference = "Stop"

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$addonRoot = $scriptRoot
if (
    ((Split-Path -Leaf $scriptRoot).ToLowerInvariant() -eq "addon") -and
    (Test-Path (Join-Path (Split-Path -Parent $scriptRoot) "tools"))
) {
    $addonRoot = Split-Path -Parent $scriptRoot
}
$venvRoot = Join-Path $addonRoot $VenvPath
$helperPython = Join-Path $venvRoot "Scripts\python.exe"
$modelName = if ($env:MAXLOGIC_XTTS_V2_MODEL_NAME) { $env:MAXLOGIC_XTTS_V2_MODEL_NAME } else { "tts_models/multilingual/multi-dataset/xtts_v2" }

function Get-PythonLauncher {
    param(
        [string]$PythonVersion
    )

    if ($PythonVersion -and $PythonVersion -ne "auto") {
        if (Get-Command py -ErrorAction SilentlyContinue) {
            return [pscustomobject]@{
                command = "py"
                arguments = @("-$PythonVersion")
            }
        }
        if (Get-Command python -ErrorAction SilentlyContinue) {
            return [pscustomobject]@{
                command = "python"
                arguments = @()
            }
        }
        if (Get-Command python3 -ErrorAction SilentlyContinue) {
            return [pscustomobject]@{
                command = "python3"
                arguments = @()
            }
        }
        throw "Python $PythonVersion is required, but no Python launcher was found."
    }

    if (Get-Command py -ErrorAction SilentlyContinue) {
        $preferredVersions = @("3.14", "3.13", "3.12", "3.11", "3.10")
        $launcherListing = & py -0p 2>$null
        foreach ($version in $preferredVersions) {
            if ($launcherListing -match [regex]::Escape("-V:$version")) {
                return [pscustomobject]@{
                    command = "py"
                    arguments = @("-$version")
                }
            }
        }
    }

    if (Get-Command python -ErrorAction SilentlyContinue) {
        return [pscustomobject]@{
            command = "python"
            arguments = @()
        }
    }
    if (Get-Command python3 -ErrorAction SilentlyContinue) {
        return [pscustomobject]@{
            command = "python3"
            arguments = @()
        }
    }
    throw "Python 3.10 or later is required, but no compatible Python launcher was found."
}

function Resolve-Provider {
    param(
        [string]$Provider
    )

    if ($Provider -and $Provider -ne "auto") {
        return $Provider
    }
    if ($env:MAXLOGIC_XTTS_V2_PROVIDER) {
        return $env:MAXLOGIC_XTTS_V2_PROVIDER
    }
    if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
        return "cuda"
    }
    return "cpu"
}

if ((Test-Path $helperPython) -and -not $Recreate) {
    Write-Host "Reusing helper venv at $venvRoot"
} else {
    if (Test-Path $venvRoot) {
        Remove-Item $venvRoot -Recurse -Force
    }
    $launcher = Get-PythonLauncher -PythonVersion $PythonVersion
    $launcherArgs = @($launcher.arguments)
    Write-Host "Creating helper venv at $venvRoot using $($launcher.command) $($launcher.arguments -join ' ')"
    & $launcher.command @launcherArgs -m venv $venvRoot
}

& $helperPython -m pip install --upgrade pip setuptools wheel

$resolvedProvider = Resolve-Provider -Provider $Provider

switch ($resolvedProvider) {
    "cuda" {
        $torchIndexUrl = "https://download.pytorch.org/whl/cu128"
    }
    "cpu" {
        $torchIndexUrl = "https://download.pytorch.org/whl/cpu"
    }
    default {
        throw "Unsupported XTTS provider: $resolvedProvider"
    }
}

Write-Host "Installing the latest PyTorch runtime for provider '$resolvedProvider'"
& $helperPython -m pip install --upgrade torch torchaudio --index-url $torchIndexUrl

Write-Host "Installing the latest Coqui TTS helper runtime"
& $helperPython -m pip install --upgrade torchcodec "coqui-tts[codec]"

Write-Host "Preparing XTTS v2 model cache"
$env:COQUI_TOS_AGREED = "1"
@'
import os
import shutil
import torch
import transformers.pytorch_utils as pytorch_utils

if not hasattr(pytorch_utils, "isin_mps_friendly"):
    pytorch_utils.isin_mps_friendly = torch.isin

from TTS.api import TTS

model_name = os.environ.get("MAXLOGIC_XTTS_V2_MODEL_NAME", "tts_models/multilingual/multi-dataset/xtts_v2")
model_dir = os.path.join(
    os.environ.get("LOCALAPPDATA", ""),
    "tts",
    model_name.replace("/", "--"),
)
model_path = os.path.join(model_dir, "model.pth")
if os.path.isfile(model_path) and os.path.getsize(model_path) == 0:
    shutil.rmtree(model_dir, ignore_errors=True)

TTS(model_name, gpu=False)
'@ | & $helperPython -

Write-Host "Verifying XTTS helper runtime"
@'
import json
import torch

payload = {
    "torch": torch.__version__,
    "cuda": torch.version.cuda,
    "cudaAvailable": bool(torch.cuda.is_available()),
    "provider": "cuda" if torch.cuda.is_available() else "cpu",
}
print(json.dumps(payload))
'@ | & $helperPython -

Write-Host ""
Write-Host "Helper environment ready."
Write-Host "Python: $helperPython"
Write-Host "This tracks the latest released Coqui TTS package from PyPI."
Write-Host "Set MAXLOGIC_XTTS_V2_HELPER_PYTHON to override discovery if needed."
@{
    helperPython = $helperPython
    provider = $resolvedProvider
} | ConvertTo-Json -Depth 4
