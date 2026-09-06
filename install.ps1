<#
.SYNOPSIS
    One-Click Installer for ATK-DL16 Logic Analyzer MCP Server.

.DESCRIPTION
    Installs Python dependencies, registers the CLI command, embeds prebuilt binaries,
    and configures MCP clients (Codex, Claude Desktop, Cursor).

.PARAMETER Client
    Target MCP client to configure: 'codex', 'claude', 'cursor', or 'none'. Default is 'codex'.
#>

param (
    [string]$Client = "codex"
)

$ErrorActionPreference = "Stop"

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  ATK-DL16 Logic Analyzer MCP Server - One-Click Installer" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

# 1. Check Python
try {
    $pyVersion = & python --version 2>&1
    Write-Host "[1/4] Found Python: $pyVersion" -ForegroundColor Green
} catch {
    Write-Error "[Error] Python 3.9+ is required but not found in PATH."
}

# 2. Bundle Prebuilt Binary if needed
$binDir = Join-Path $PSScriptRoot "atk_dl16_mcp\bin"
$binExe = Join-Path $binDir "atk-dl16.exe"
if (-not (Test-Path $binExe)) {
    $relExe = Join-Path $PSScriptRoot "build\Release\atk-dl16.exe"
    if (Test-Path $relExe) {
        if (-not (Test-Path $binDir)) { New-Item -ItemType Directory -Path $binDir -Force | Out-Null }
        Copy-Item $relExe $binExe -Force
        Write-Host "[2/4] Bundled static prebuilt binary: $binExe" -ForegroundColor Green
    } else {
        Write-Host "[2/4] Note: Local build binary not found. Will rely on system PATH." -ForegroundColor Yellow
    }
} else {
    Write-Host "[2/4] Prebuilt binary verified: $binExe" -ForegroundColor Green
}

# 3. Install Package
Write-Host "[3/4] Installing package in editable mode via pip..." -ForegroundColor Cyan
& python -m pip install -e $PSScriptRoot

# 4. Check Hardware and Print Config
Write-Host "[4/4] Verifying hardware status..." -ForegroundColor Cyan
try {
    & atk-dl16-mcp status
} catch {
    Write-Host "[Warning] Device not connected or claimed, but MCP server is ready." -ForegroundColor Yellow
}

if ($Client -ne "none") {
    Write-Host "`nConfiguring target client: $Client" -ForegroundColor Cyan
    & atk-dl16-mcp install --client $Client
} else {
    Write-Host "`nMCP Configuration snippet:" -ForegroundColor Cyan
    & atk-dl16-mcp config --client codex
}

Write-Host "`n============================================================" -ForegroundColor Green
Write-Host "  ATK-DL16 MCP Server Installation Complete!" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
