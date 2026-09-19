param([string]$Python = '')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not $Python) {
    $bundledPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
    if (Test-Path -LiteralPath $bundledPython) { $Python = $bundledPython } else { $Python = 'python' }
}
& $Python -m venv .venv
if ($LASTEXITCODE -ne 0) { throw 'Failed to create Python virtual environment.' }
& '.\.venv\Scripts\python.exe' -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Failed to install dependencies.' }
& (Join-Path $PSScriptRoot 'Install-Shortcuts.ps1')
Write-Host 'Ready. Open Ramp Studio from the desktop or Start menu.'
