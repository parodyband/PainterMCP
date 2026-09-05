[CmdletBinding()]
param(
    [string]$InstallRoot = (Join-Path $env:USERPROFILE '.painter-mcp\venv'),
    [string]$PainterPython = '',
    [ValidateSet('codex','claude')][string[]]$Clients = @('codex','claude'),
    [switch]$ReplaceEdited
)
$ErrorActionPreference = 'Stop'
$packageRoot = Split-Path -Parent $PSScriptRoot
# In a release archive, this script is at the archive root beside its wheel.
if (Get-ChildItem -LiteralPath $PSScriptRoot -Filter 'painter_mcp-*.whl') { $packageRoot = $PSScriptRoot }
$pythonCommand = Get-Command python -ErrorAction Stop
& $pythonCommand.Source -c 'import sys; assert sys.version_info >= (3,10), "Python 3.10+ is required"'
if ($LASTEXITCODE -ne 0) { throw 'Install Python 3.10 or newer and retry.' }
if (-not (Test-Path -LiteralPath (Join-Path $InstallRoot 'Scripts\python.exe'))) {
    & $pythonCommand.Source -m venv $InstallRoot
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the installation virtual environment.' }
}
$python = Join-Path $InstallRoot 'Scripts\python.exe'
$wheels = @(Get-ChildItem -LiteralPath $packageRoot -Filter 'painter_mcp-*.whl')
if ($wheels.Count -gt 1) { throw 'Multiple wheels found; use a fresh release directory.' }
$source = if ($wheels.Count -eq 1) { $wheels[0].FullName } else { $packageRoot }
& $python -m pip install --upgrade $source
if ($LASTEXITCODE -ne 0) { throw 'Package installation failed.' }
$setupArguments = @('-m','painter_mcp','install')
if ($PainterPython) { $setupArguments += @('--painter-python', $PainterPython) }
if ($Clients.Count) { $setupArguments += '--clients'; $setupArguments += $Clients }
if ($ReplaceEdited) { $setupArguments += '--replace-edited' }
& $python @setupArguments
if ($LASTEXITCODE -ne 0) { throw 'Managed setup needs attention; review the conflict/diagnostic output above.' }
Write-Host "Restart Painter and your MCP client. Diagnose with: & '$python' -m painter_mcp doctor"
