@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%run-current-main-codex-v2.ps1" %*
exit /b %ERRORLEVEL%
