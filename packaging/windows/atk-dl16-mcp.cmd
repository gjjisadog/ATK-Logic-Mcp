@echo off
setlocal
if not defined ATK_DL16_DATA_DIR set "ATK_DL16_DATA_DIR=%LOCALAPPDATA%\ATK-DL16-MCP\data"
if not exist "%ATK_DL16_DATA_DIR%" mkdir "%ATK_DL16_DATA_DIR%" >nul 2>nul
"%~dp0runtime\python.exe" -m atk_dl16_mcp %*
exit /b %ERRORLEVEL%
