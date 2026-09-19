param([string]$Python = "")
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
if (-not $Python -and (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'release\Ramp Studio.exe'))) {
    Start-Process -FilePath (Join-Path $PSScriptRoot 'release\Ramp Studio.exe') -WorkingDirectory (Join-Path $PSScriptRoot 'release') -WindowStyle Hidden
    exit 0
}
if (-not $Python) {
    $localPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $localPython) { $Python = $localPython } else { $Python = 'python' }
}
& $Python -m host.app
if ($LASTEXITCODE -ne 0) { Write-Error "Ramp Studio exited with code $LASTEXITCODE. Install requirements with: python -m pip install -r requirements.txt" }
