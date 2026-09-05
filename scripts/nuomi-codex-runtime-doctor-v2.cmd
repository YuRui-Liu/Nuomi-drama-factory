@echo off
setlocal
set "NUOMI_DOCTOR=%~dp0nuomi-codex-runtime-doctor-v2.ps1"
if not exist "%NUOMI_DOCTOR%" (
  echo [FAIL] Runtime doctor script not found: "%NUOMI_DOCTOR%"
  exit /b 2
)
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%NUOMI_DOCTOR%" %*
exit /b %ERRORLEVEL%
