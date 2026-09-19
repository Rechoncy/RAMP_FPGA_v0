param()
$ErrorActionPreference = 'Stop'
$studioPython = Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe'
$studioIcon = Join-Path $PSScriptRoot 'host\assets\ramp-studio.ico'
$studioArguments = '-m host.app'
$studioWorkingDirectory = $PSScriptRoot
$studioExecutable = Join-Path $PSScriptRoot 'release\Ramp Studio.exe'
if (Test-Path -LiteralPath $studioExecutable) {
    $studioPython = $studioExecutable
    $studioIcon = $studioExecutable
    $studioArguments = ''
    $studioWorkingDirectory = Split-Path -Parent $studioExecutable
}
if (-not (Test-Path -LiteralPath $studioPython)) { throw 'Run Setup-RampStudio.ps1 first.' }
if (-not (Test-Path -LiteralPath $studioIcon)) { throw 'Ramp Studio icon is missing.' }
$studioShell = New-Object -ComObject WScript.Shell
$studioDestinations = @(
    (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Ramp Studio.lnk'),
    (Join-Path ([Environment]::GetFolderPath('Programs')) 'Ramp Studio.lnk'),
    (Join-Path $PSScriptRoot 'Ramp Studio.lnk')
)
foreach ($studioDestination in $studioDestinations) {
    $studioLink = $studioShell.CreateShortcut($studioDestination)
    $studioLink.TargetPath = $studioPython
    $studioLink.Arguments = $studioArguments
    $studioLink.WorkingDirectory = $studioWorkingDirectory
    $studioLink.IconLocation = "$studioIcon,0"
    $studioLink.Description = 'Ramp Studio - FPGA waveform control'
    $studioLink.WindowStyle = 1
    $studioLink.Save()
    Write-Host "Created: $studioDestination"
}
