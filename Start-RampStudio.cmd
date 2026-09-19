@echo off
pushd "%~dp0"
if exist "release\Ramp Studio.exe" (
  start "Ramp Studio" "release\Ramp Studio.exe"
  popd
  exit /b 0
)
if not exist ".venv\Scripts\pythonw.exe" (
  echo Please run Setup-RampStudio.ps1 first.
  pause
  exit /b 1
)
start "Ramp Studio" ".venv\Scripts\pythonw.exe" -m host.app
popd
