$expectedVersion = '__PAINTER_MCP_VERSION__'
$repository = 'parodyband/PainterMCP'
$assetName = "painter-mcp-$expectedVersion.zip"
$launcherDirectory = Split-Path -Parent $env:PAINTER_MCP_INSTALLER_LAUNCHER
$temporary = $null
try {
    $packageDirectory = $launcherDirectory
    if (-not (Test-Path -LiteralPath (Join-Path $packageDirectory 'manifest.json'))) {
        Write-Host 'Downloading and verifying the complete Painter MCP package...'
        $temporary = Join-Path ([IO.Path]::GetTempPath()) ('painter-mcp-installer-' + [Guid]::NewGuid().ToString('N'))
        [IO.Directory]::CreateDirectory($temporary) | Out-Null
        $archive = Join-Path $temporary $assetName
        if ($env:PAINTER_MCP_INSTALLER_ARCHIVE) {
            if (-not $env:PAINTER_MCP_INSTALLER_SHA256) { throw 'A supplied archive requires PAINTER_MCP_INSTALLER_SHA256.' }
            Copy-Item -LiteralPath $env:PAINTER_MCP_INSTALLER_ARCHIVE -Destination $archive
            Assert-InstallerHash $archive $env:PAINTER_MCP_INSTALLER_SHA256
        } else {
            $releasePath = Join-Path $temporary 'release.json'
            Save-InstallerDownload "https://api.github.com/repos/$repository/releases/tags/v$expectedVersion" $releasePath 2097152
            $release = Get-Content -LiteralPath $releasePath -Raw | ConvertFrom-Json
            if ($release.tag_name -ne "v$expectedVersion" -or $release.draft -or $release.prerelease) { throw 'Expected a published stable release matching this launcher.' }
            $manifests = @($release.assets | Where-Object name -eq 'release-manifest.json')
            $packages = @($release.assets | Where-Object name -eq $assetName)
            if ($manifests.Count -ne 1 -or $packages.Count -ne 1) { throw 'Release is missing its matching package or manifest.' }
            $baseUri = "https://github.com/$repository/releases/download/v$expectedVersion"
            if ($manifests[0].browser_download_url -ne "$baseUri/release-manifest.json" -or $packages[0].browser_download_url -ne "$baseUri/$assetName") { throw 'Release asset URL identity mismatch.' }
            $manifestPath = Join-Path $temporary 'release-manifest.json'
            Save-InstallerDownload "$baseUri/release-manifest.json" $manifestPath 1048576
            if ($manifests[0].digest) { Assert-InstallerHash $manifestPath ([string]$manifests[0].digest).Replace('sha256:','') }
            $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
            if ($manifest.schema_version -ne 1 -or $manifest.name -ne 'painter-mcp' -or $manifest.version -ne $expectedVersion -or $manifest.package.name -ne $assetName) { throw 'Release manifest identity mismatch.' }
            if ([long]$manifest.package.size -ne [long]$packages[0].size -or [long]$manifest.package.size -le 0 -or [long]$manifest.package.size -gt 134217728) { throw 'Invalid release archive size.' }
            if ($packages[0].digest -and $packages[0].digest -ne "sha256:$($manifest.package.sha256)") { throw 'GitHub and the manifest disagree about the package hash.' }
            Save-InstallerDownload "$baseUri/$assetName" $archive
            if ((Get-Item -LiteralPath $archive).Length -ne [long]$manifest.package.size) { throw 'Release download was incomplete.' }
            Assert-InstallerHash $archive $manifest.package.sha256
        }
        if ((Get-Item -LiteralPath $archive).Length -gt 134217728) { throw 'Release archive exceeds its size limit.' }
        $extract = Join-Path $temporary 'package'
        Expand-InstallerZip $archive $extract
        $packageDirectory = Join-Path $extract "painter-mcp-$expectedVersion"
    }
    $null = Assert-InstallerManifest $packageDirectory $expectedVersion
    $arguments = @{}
    if ($env:PAINTER_MCP_INSTALLER_ROOT) { $arguments.InstallRoot = $env:PAINTER_MCP_INSTALLER_ROOT }
    if ($env:PAINTER_MCP_INSTALLER_PAINTER_PYTHON) { $arguments.PainterPython = $env:PAINTER_MCP_INSTALLER_PAINTER_PYTHON }
    if ($env:PAINTER_MCP_INSTALLER_USER_HOME) { $arguments.UserHome = $env:PAINTER_MCP_INSTALLER_USER_HOME }
    if ($env:PAINTER_MCP_INSTALLER_CLIENTS) { $arguments.Clients = $env:PAINTER_MCP_INSTALLER_CLIENTS.Split(',') }
    if ($env:PAINTER_MCP_INSTALLER_FORCE_PRIVATE_PYTHON -eq '1') { $arguments.ForcePrivatePython = $true }
    & (Join-Path $packageDirectory 'install.ps1') @arguments
} finally {
    if ($temporary) { Remove-InstallerTemp $temporary }
}
