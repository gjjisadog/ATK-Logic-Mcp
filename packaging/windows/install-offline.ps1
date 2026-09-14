<#
.SYNOPSIS
    Install the ATK-DL16 MCP Windows offline bundle without network access.

.DESCRIPTION
    This script only copies files already present in the release archive and
    updates local MCP configuration files. It never invokes pip, npm, or a
    package registry.
#>

[CmdletBinding()]
param (
    [ValidateSet("all", "codex", "claude-code", "claude-code-user", "claude-desktop", "pi", "pi-global", "cursor", "none")]
    [string]$Client = "all",
    [string]$ProjectRoot = "",
    [string]$InstallRoot = ""
)

$ErrorActionPreference = "Stop"

$bundleRoot = (Resolve-Path $PSScriptRoot).Path.TrimEnd("\")
if (-not $InstallRoot) {
    $localDataRoot = $env:LOCALAPPDATA
    if (-not $localDataRoot) {
        $localDataRoot = Join-Path $env:USERPROFILE "AppData\Local"
    }
    $InstallRoot = Join-Path $localDataRoot "ATK-DL16-MCP"
}
$InstallRoot = [IO.Path]::GetFullPath($InstallRoot)

$runtimeSource = Join-Path $bundleRoot "runtime"
$piSource = Join-Path $bundleRoot "pi\node_modules"
$runtimeTarget = Join-Path $InstallRoot "runtime"
$dataTarget = Join-Path $InstallRoot "data"
$pythonSource = Join-Path $runtimeSource "python.exe"
$pythonTarget = Join-Path $runtimeTarget "python.exe"

if (-not (Test-Path -LiteralPath $pythonSource)) {
    throw "Offline bundle is incomplete: missing $pythonSource"
}
if (-not (Test-Path -LiteralPath (Join-Path $runtimeSource "Lib\site-packages\atk_dl16_mcp"))) {
    throw "Offline bundle is incomplete: missing the ATK-DL16 Python package."
}

function Get-FullPathString([string]$PathValue) {
    return [IO.Path]::GetFullPath($PathValue).TrimEnd("\")
}

$bundleFullPath = Get-FullPathString $bundleRoot
$installFullPath = Get-FullPathString $InstallRoot
New-Item -ItemType Directory -Path $InstallRoot, $dataTarget -Force | Out-Null

if ($bundleFullPath -ine $installFullPath) {
    Write-Host "[1/5] Copying the embedded runtime to $InstallRoot..." -ForegroundColor Cyan
    Copy-Item -LiteralPath $runtimeSource -Destination $InstallRoot -Recurse -Force
    if (Test-Path -LiteralPath $piSource) {
        Copy-Item -LiteralPath (Join-Path $bundleRoot "pi") -Destination $InstallRoot -Recurse -Force
    }
    foreach ($fileName in @("README-OFFLINE.md", "LICENSE", "manifest.json", "install-offline.ps1", "install-offline.cmd", "atk-dl16-mcp.cmd")) {
        $sourceFile = Join-Path $bundleRoot $fileName
        if (Test-Path -LiteralPath $sourceFile) {
            Copy-Item -LiteralPath $sourceFile -Destination $InstallRoot -Force
        }
    }
} else {
    Write-Host "[1/5] Bundle is already installed at $InstallRoot..." -ForegroundColor Cyan
}

$pythonTarget = Join-Path $InstallRoot "runtime\python.exe"
$dataTarget = Join-Path $InstallRoot "data"
New-Item -ItemType Directory -Path $dataTarget -Force | Out-Null
$env:ATK_DL16_DATA_DIR = $dataTarget

function Invoke-McpInstaller([string]$TargetClient, [string]$TargetProjectRoot = "") {
    $arguments = @("-m", "atk_dl16_mcp", "install", "--client", $TargetClient)
    if ($TargetProjectRoot) {
        $arguments += @("--project-root", $TargetProjectRoot)
    }
    & $pythonTarget @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "MCP configuration failed for $TargetClient (exit code $LASTEXITCODE)."
    }
}

function Get-PiAgentDirectory {
    if ($env:PI_CODING_AGENT_DIR) {
        return [IO.Path]::GetFullPath($env:PI_CODING_AGENT_DIR)
    }
    return Join-Path $env:USERPROFILE ".pi\agent"
}

function Write-PiSettingsWithExtension {
    $piAgentDirectory = Get-PiAgentDirectory
    $piNpmRoot = Join-Path $piAgentDirectory "npm"
    $targetNodeModules = Join-Path $piNpmRoot "node_modules"
    New-Item -ItemType Directory -Path $targetNodeModules -Force | Out-Null

    if (-not (Test-Path -LiteralPath $piSource)) {
        Write-Warning "Pi extension payload is missing from this bundle; Pi MCP support was not staged."
        return
    }

    Write-Host "[4/5] Copying the bundled Pi MCP extension (offline)..." -ForegroundColor Cyan
    Get-ChildItem -LiteralPath $piSource -Force |
        Where-Object { $_.Name -ne ".bin" } |
        ForEach-Object {
            Copy-Item -LiteralPath $_.FullName -Destination $targetNodeModules -Recurse -Force
        }

    $rootPackageJson = Join-Path $piNpmRoot "package.json"
    if (-not (Test-Path -LiteralPath $rootPackageJson)) {
        '{"name":"pi-extensions","private":true}' | Set-Content -LiteralPath $rootPackageJson -Encoding UTF8
    }

    $settingsPath = Join-Path $piAgentDirectory "settings.json"
    $settings = [PSCustomObject]@{}
    if (Test-Path -LiteralPath $settingsPath) {
        try {
            $settings = Get-Content -LiteralPath $settingsPath -Raw | ConvertFrom-Json
            if ($null -eq $settings) {
                $settings = [PSCustomObject]@{}
            }
        } catch {
            $backupPath = "$settingsPath.bak"
            $suffix = 1
            while (Test-Path -LiteralPath $backupPath) {
                $backupPath = "$settingsPath.bak.$suffix"
                $suffix++
            }
            Copy-Item -LiteralPath $settingsPath -Destination $backupPath -Force
            Write-Warning "Pi settings JSON was invalid; the original was backed up to $backupPath."
            $settings = [PSCustomObject]@{}
        }
    }

    $packageSource = "npm:pi-mcp-extension@1.5.0"
    $packages = @()
    $packageProperty = $settings.PSObject.Properties["packages"]
    if ($packageProperty -and $null -ne $packageProperty.Value) {
        $packages = @($packageProperty.Value)
    }
    $alreadyConfigured = $false
    foreach ($package in $packages) {
        if ($package -is [string] -and $package -match "^npm:pi-mcp-extension(?:@|$)") {
            $alreadyConfigured = $true
        } elseif ($package -and $package.PSObject.Properties["source"] -and $package.source -match "^npm:pi-mcp-extension(?:@|$)") {
            $alreadyConfigured = $true
        }
    }
    if (-not $alreadyConfigured) {
        $packages += $packageSource
    }
    if ($packageProperty) {
        $settings.packages = $packages
    } else {
        $settings | Add-Member -NotePropertyName packages -NotePropertyValue $packages
    }

    $settingsParent = Split-Path -Parent $settingsPath
    New-Item -ItemType Directory -Path $settingsParent -Force | Out-Null
    $tempSettingsPath = Join-Path $settingsParent (".settings-" + [guid]::NewGuid().ToString("N") + ".tmp")
    try {
        $settings | ConvertTo-Json -Depth 100 | Set-Content -LiteralPath $tempSettingsPath -Encoding UTF8
        Move-Item -LiteralPath $tempSettingsPath -Destination $settingsPath -Force
    } finally {
        if (Test-Path -LiteralPath $tempSettingsPath) {
            Remove-Item -LiteralPath $tempSettingsPath -Force
        }
    }
    Write-Host "     Pi extension registered in $settingsPath" -ForegroundColor Green
}

Write-Host "[2/5] Using data directory $dataTarget..." -ForegroundColor Cyan
if ($Client -eq "none") {
    Write-Host "[3/5] Client configuration skipped by request." -ForegroundColor Yellow
} elseif ($Client -eq "all") {
    if ($ProjectRoot) {
        Invoke-McpInstaller "claude-code" $ProjectRoot
        Invoke-McpInstaller "codex"
        Invoke-McpInstaller "pi" $ProjectRoot
    } else {
        Invoke-McpInstaller "claude-code-user"
        Invoke-McpInstaller "codex"
        Invoke-McpInstaller "pi-global"
    }
    Write-PiSettingsWithExtension
} else {
    $targetProject = $ProjectRoot
    if ($Client -in @("claude-code", "pi") -and -not $targetProject) {
        $targetProject = (Get-Location).Path
    }
    Invoke-McpInstaller $Client $targetProject
    if ($Client -in @("pi", "pi-global")) {
        Write-PiSettingsWithExtension
    }
}

Write-Host "[5/5] Offline installation complete." -ForegroundColor Green
Write-Host "     Runtime: $pythonTarget"
Write-Host "     Data:    $dataTarget"
Write-Host "     Restart Claude Code, Codex, or Pi to load the MCP server."
