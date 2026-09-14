@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-offline.ps1" %*
exit /b %ERRORLEVEL%
