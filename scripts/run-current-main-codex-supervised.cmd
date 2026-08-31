@echo off
setlocal
set "NUOMI_RUNNER=%~dp0run-current-main-codex-supervised.ps1"
if not exist "%NUOMI_RUNNER%" (
  echo [FAIL] Supervised runner not found: "%NUOMI_RUNNER%"
  exit /b 2
)
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%NUOMI_RUNNER%" %*
exit /b %ERRORLEVEL%
