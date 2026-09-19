param()
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$studioPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $studioPython)) { throw 'Run Setup-RampStudio.ps1 first.' }
& $studioPython -m pip install -r requirements.txt -r requirements-packaging.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
& $studioPython scripts\collect_licenses.py
if ($LASTEXITCODE -ne 0) { throw 'License collection failed.' }
& $studioPython -m PyInstaller --noconfirm --distpath release --workpath build\pyinstaller RampStudio.spec
if ($LASTEXITCODE -ne 0) { throw 'Executable build failed.' }
& $studioPython scripts\verify_executable.py
if ($LASTEXITCODE -ne 0) { throw 'Portable executable verification failed.' }
Copy-Item -LiteralPath 'docs\Windows便携版说明.md' -Destination 'release\使用说明.md' -Force
Copy-Item -LiteralPath 'boards\xc7a35t_four_dac.json' -Destination 'release\四路DAC板卡.json' -Force
Copy-Item -LiteralPath 'packaging\licenses' -Destination 'release' -Recurse -Force
Compress-Archive -LiteralPath @('release\Ramp Studio.exe','release\使用说明.md','release\四路DAC板卡.json','release\licenses') -DestinationPath 'release\Ramp-Studio-Windows-x64.zip' -Force
& $studioPython scripts\package_documentation.py
if ($LASTEXITCODE -ne 0) { throw 'Documentation packaging failed.' }
Write-Host 'Ready: release\Ramp Studio.exe and release\Ramp-Studio-Windows-x64.zip'
