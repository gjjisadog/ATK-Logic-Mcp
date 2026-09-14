<#
.SYNOPSIS
    Build the self-contained Windows x64 offline release archive.

.DESCRIPTION
    The build host may access Python and npm registries once. The resulting zip
    contains an embeddable Python runtime, all Python wheels installed into that
    runtime, the statically linked ATK-DL16 CLI, and the Pi MCP extension plus
    its runtime dependencies. End users do not need Python, pip, Node, npm, or
    network access to install the archive.
#>

[CmdletBinding()]
param (
    [string]$Version = "1.1.0",
    [string]$PythonVersion = "3.12.10",
    [string]$PythonCommand = "python",
    [string]$OutputDirectory = ""
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\.." )).Path
if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $repoRoot "dist"
}
$OutputDirectory = [IO.Path]::GetFullPath($OutputDirectory)

$pythonCommandInfo = Get-Command $PythonCommand -ErrorAction Stop
$pythonExe = $pythonCommandInfo.Source
if (-not $pythonExe) {
    $pythonExe = $pythonCommandInfo.Path
}

$pythonRuntimeVersion = (& $pythonExe -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')").Trim()
if ($pythonRuntimeVersion -notmatch "^3\.12\.") {
    throw "Python 3.12 is required to build the embedded runtime package; found $pythonRuntimeVersion at $pythonExe."
}

$cmakeInfo = Get-Command cmake -ErrorAction Stop
$npmInfo = Get-Command npm -ErrorAction Stop
if (-not $cmakeInfo -or -not $npmInfo) {
    throw "Both CMake and npm are required on the release build host."
}

$buildDirectory = Join-Path $repoRoot "build"
$nativeCli = Join-Path $buildDirectory "Release\atk-dl16.exe"
if (-not (Test-Path -LiteralPath (Join-Path $buildDirectory "CMakeCache.txt"))) {
    Write-Host "[1/9] Configuring CMake (Visual Studio 2022 x64)..." -ForegroundColor Cyan
    & $cmakeInfo.Source -S $repoRoot -B $buildDirectory -G "Visual Studio 17 2022" -A x64
    if ($LASTEXITCODE -ne 0) {
        throw "CMake configuration failed with exit code $LASTEXITCODE."
    }
} else {
    Write-Host "[1/9] Reusing existing CMake build directory..." -ForegroundColor Cyan
}

Write-Host "[2/9] Building the native CLI..." -ForegroundColor Cyan
& $cmakeInfo.Source --build $buildDirectory --config Release --target atk-dl16
if ($LASTEXITCODE -ne 0) {
    throw "Native CLI build failed with exit code $LASTEXITCODE."
}
if (-not (Test-Path -LiteralPath $nativeCli)) {
    throw "Expected native CLI was not produced: $nativeCli"
}

$stagingParent = Join-Path ([IO.Path]::GetTempPath()) ("atk-dl16-offline-" + [guid]::NewGuid().ToString("N"))
$packageName = "ATK-DL16-MCP-Windows-x64-v$Version"
$packageRoot = Join-Path $stagingParent $packageName
$runtimeRoot = Join-Path $packageRoot "runtime"
$sitePackages = Join-Path $runtimeRoot "Lib\site-packages"
$wheelhouse = Join-Path $stagingParent "wheelhouse"
$piStage = Join-Path $stagingParent "pi-stage"
$pythonZip = Join-Path $stagingParent "python-embed.zip"
$archivePath = Join-Path $OutputDirectory "$packageName.zip"

try {
    New-Item -ItemType Directory -Path $packageRoot, $wheelhouse, $piStage, $OutputDirectory -Force | Out-Null

    Write-Host "[3/9] Downloading Python $PythonVersion embeddable runtime..." -ForegroundColor Cyan
    $pythonUrl = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip"
    Invoke-WebRequest -Uri $pythonUrl -OutFile $pythonZip
    Expand-Archive -LiteralPath $pythonZip -DestinationPath $runtimeRoot -Force
    New-Item -ItemType Directory -Path $sitePackages | Out-Null

    $pthFile = Get-ChildItem -LiteralPath $runtimeRoot -Filter "*._pth" -File | Select-Object -First 1
    if (-not $pthFile) {
        throw "The embeddable Python archive does not contain a ._pth file."
    }
    @(
        "python312.zip",
        ".",
        "Lib\site-packages",
        "import site"
    ) -join "`r`n" | Set-Content -LiteralPath $pthFile.FullName -Encoding ASCII

    Write-Host "[4/9] Downloading Python dependency wheels for Windows x64..." -ForegroundColor Cyan
    & $pythonExe -m pip download `
        --only-binary=:all: `
        --dest $wheelhouse `
        --platform win_amd64 `
        --python-version 3.12 `
        --implementation cp `
        --abi cp312 `
        "numpy>=1.24.0" "mcp>=1.0.0" "pyyaml>=6.0"
    if ($LASTEXITCODE -ne 0) {
        throw "Python wheel download failed with exit code $LASTEXITCODE."
    }

    Write-Host "[5/9] Installing Python dependencies into the embedded runtime..." -ForegroundColor Cyan
    & $pythonExe -m pip install `
        --no-index `
        --no-cache-dir `
        --only-binary=:all: `
        --find-links $wheelhouse `
        --target $sitePackages `
        "numpy>=1.24.0" "mcp>=1.0.0" "pyyaml>=6.0"
    if ($LASTEXITCODE -ne 0) {
        throw "Offline Python dependency installation failed with exit code $LASTEXITCODE."
    }

    Write-Host "[6/9] Copying MCP server, analysis modules, and native CLI..." -ForegroundColor Cyan
    Copy-Item -Path (Join-Path $repoRoot "atk_dl16_mcp") -Destination $sitePackages -Recurse -Force
    Copy-Item -Path (Join-Path $repoRoot "analysis") -Destination $sitePackages -Recurse -Force
    $packageBinaryDirectory = Join-Path $sitePackages "atk_dl16_mcp\bin"
    New-Item -ItemType Directory -Path $packageBinaryDirectory -Force | Out-Null
    Copy-Item -LiteralPath $nativeCli -Destination (Join-Path $packageBinaryDirectory "atk-dl16.exe") -Force

    Write-Host "[7/9] Staging the Pi MCP extension and its dependencies..." -ForegroundColor Cyan
    & $npmInfo.Source install `
        --prefix $piStage `
        --legacy-peer-deps `
        --ignore-scripts `
        --no-audit `
        --no-fund `
        "pi-mcp-extension@1.5.0"
    if ($LASTEXITCODE -ne 0) {
        throw "Pi MCP extension staging failed with exit code $LASTEXITCODE."
    }
    $piPackageRoot = Join-Path $packageRoot "pi"
    New-Item -ItemType Directory -Path $piPackageRoot | Out-Null
    Copy-Item -Path (Join-Path $piStage "node_modules") -Destination $piPackageRoot -Recurse -Force

    Write-Host "[8/9] Adding offline installer, launcher, documentation, and manifest..." -ForegroundColor Cyan
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot "install-offline.ps1") -Destination $packageRoot -Force
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot "install-offline.cmd") -Destination $packageRoot -Force
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot "atk-dl16-mcp.cmd") -Destination $packageRoot -Force
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot "README-OFFLINE.md") -Destination $packageRoot -Force
    Copy-Item -LiteralPath (Join-Path $repoRoot "LICENSE") -Destination $packageRoot -Force

    $nativeHash = (Get-FileHash -LiteralPath $nativeCli -Algorithm SHA256).Hash
    $manifest = [ordered]@{
        product = "ATK-DL16 MCP Server"
        version = $Version
        platform = "windows-x64"
        offline = $true
        embeddedPython = $PythonVersion
        piMcpExtension = "1.5.0"
        nativeCliSha256 = $nativeHash
    }
    $manifest | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $packageRoot "manifest.json") -Encoding UTF8

    Get-ChildItem -LiteralPath $packageRoot -Directory -Recurse -Force |
        Where-Object { $_.Name -eq "__pycache__" } |
        Remove-Item -Recurse -Force

    $embeddedPython = Join-Path $runtimeRoot "python.exe"
    & $embeddedPython -c "import atk_dl16_mcp, analysis, numpy, mcp, yaml; print('embedded runtime import OK')"
    if ($LASTEXITCODE -ne 0) {
        throw "The embedded runtime could not import the packaged Python modules."
    }
    & $embeddedPython -m atk_dl16_mcp config --client codex | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw "The embedded MCP configuration command failed."
    }

    if (Test-Path -LiteralPath $archivePath) {
        Remove-Item -LiteralPath $archivePath -Force
    }
    Write-Host "[9/9] Creating $archivePath..." -ForegroundColor Cyan
    Compress-Archive -Path (Join-Path $packageRoot "*") -DestinationPath $archivePath -CompressionLevel Optimal
    $archiveHash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash
    $checksumPath = Join-Path $OutputDirectory "SHA256SUMS.txt"
    "$archiveHash  $([IO.Path]::GetFileName($archivePath))" | Set-Content -LiteralPath $checksumPath -Encoding ASCII

    Write-Host "Offline package created successfully:" -ForegroundColor Green
    Write-Host "  $archivePath"
    Write-Host "  SHA256: $archiveHash"
} finally {
    if (Test-Path -LiteralPath $stagingParent) {
        Remove-Item -LiteralPath $stagingParent -Recurse -Force
    }
}
