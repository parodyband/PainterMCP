[CmdletBinding()]
param(
    [string]$InstallRoot = '',
    [string]$PainterPython = '',
    [ValidateSet('codex','claude')][string[]]$Clients = @('codex','claude'),
    [string]$UserHome = '',
    [switch]$ForcePrivatePython,
    [switch]$ReplaceEdited
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'installer-common.ps1')
if (-not [Environment]::Is64BitOperatingSystem) { throw 'Painter MCP requires 64-bit Windows.' }
$stateRoot = if ($env:PAINTER_MCP_HOME) { [IO.Path]::GetFullPath($env:PAINTER_MCP_HOME) } else { Join-Path $env:USERPROFILE '.painter-mcp' }
if (-not $InstallRoot) { $InstallRoot = Join-Path $stateRoot 'venv' }
$InstallRoot = [IO.Path]::GetFullPath($InstallRoot)
[IO.Directory]::CreateDirectory($stateRoot) | Out-Null
$setupLock = $null
try {
    $setupLock = [IO.File]::Open((Join-Path $stateRoot 'installer.lock'),[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)
} catch { throw 'Another Painter MCP setup is running. Wait for it to finish, then retry.' }
try {
    $packageRoot = Split-Path -Parent $PSScriptRoot
    if (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'manifest.json')) {
        $packageRoot = $PSScriptRoot
        $null = Assert-InstallerManifest $packageRoot
    }
    $python = Join-Path $InstallRoot 'Scripts\python.exe'
    if (-not (Test-InstallerPython $python)) {
        $basePython = Get-InstallerPython $stateRoot -ForcePrivate:$ForcePrivatePython
        Write-Host 'Preparing the isolated Painter MCP environment...'
        & $basePython -m venv $InstallRoot
        if ($LASTEXITCODE -ne 0) { throw 'Could not create the installation environment.' }
    }
    $wheels = @(Get-ChildItem -LiteralPath $packageRoot -Filter 'painter_mcp-*.whl')
    if ($wheels.Count -gt 1) { throw 'Multiple wheels found; use a fresh release directory.' }
    $source = if ($wheels.Count -eq 1) { $wheels[0].FullName } else { $packageRoot }
    Write-Host 'Installing Painter MCP and its dependencies...'
    & $python -m pip install --upgrade --disable-pip-version-check --no-input --only-binary=:all: $source
    if ($LASTEXITCODE -ne 0) { throw 'Package installation failed. Check your internet connection and the messages above.' }
    $setupArguments = @('-m','painter_mcp','install')
    if ($PainterPython) { $setupArguments += @('--painter-python', $PainterPython) }
    if ($UserHome) { $setupArguments += @('--user-home', $UserHome) }
    if ($Clients.Count) { $setupArguments += '--clients'; $setupArguments += $Clients }
    if ($ReplaceEdited) { $setupArguments += '--replace-edited' }
    & $python @setupArguments
    if ($LASTEXITCODE -ne 0) { throw 'Managed setup needs attention. Review the reported conflicts; your edits have been preserved.' }
    Write-Host ''
    Write-Host 'Painter MCP is installed. Your current Painter project has not been changed.' -ForegroundColor Green
    Write-Host "Restart Painter and your AI client when ready. Diagnostics: & '$python' -m painter_mcp doctor"
} finally { $setupLock.Dispose() }
