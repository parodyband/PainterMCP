# Shared by the release entry point and setup. Compatible with Windows PowerShell 5.1.
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

function Get-InstallerHash([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Assert-InstallerHash([string]$Path, [string]$Expected) {
    if ($Expected -notmatch '^[a-fA-F0-9]{64}$' -or (Get-InstallerHash $Path) -ne $Expected.ToLowerInvariant()) {
        throw "SHA-256 verification failed: $([IO.Path]::GetFileName($Path))"
    }
}

function Save-InstallerDownload([string]$Uri, [string]$Path, [long]$MaximumBytes = 134217728) {
    $allowedHosts = @('api.github.com','github.com','release-assets.githubusercontent.com','objects.githubusercontent.com','api.nuget.org','globalcdn.nuget.org')
    $current = [Uri]$Uri
    $watch = [Diagnostics.Stopwatch]::StartNew()
    for ($redirect = 0; $redirect -lt 6; $redirect++) {
        if ($current.Scheme -ne 'https' -or $current.Host -notin $allowedHosts -or $current.UserInfo -or $current.Port -ne 443) {
            throw 'The installer refused an untrusted download URL.'
        }
        $request = [Net.HttpWebRequest]::Create($current)
        $request.UserAgent = 'PainterMCP-Installer'
        $request.Timeout = 15000
        $request.ReadWriteTimeout = 15000
        $request.AllowAutoRedirect = $false
        $response = $request.GetResponse()
        try {
            if ([int]$response.StatusCode -in @(301,302,303,307,308)) {
                $current = [Uri]::new($current, $response.Headers['Location'])
                continue
            }
            if ([long]$response.ContentLength -gt $MaximumBytes) { throw 'Download exceeds the installer size limit.' }
            $inputStream = $response.GetResponseStream()
            $outputStream = [IO.File]::Create($Path)
            try {
                $buffer = New-Object byte[] 65536
                [long]$received = 0
                while (($count = $inputStream.Read($buffer,0,$buffer.Length)) -gt 0) {
                    $received += $count
                    if ($received -gt $MaximumBytes -or $watch.Elapsed.TotalSeconds -gt 120) {
                        throw 'Download exceeded its byte or time limit.'
                    }
                    $outputStream.Write($buffer,0,$count)
                }
            } finally { $outputStream.Dispose(); $inputStream.Dispose() }
            return
        } finally { $response.Dispose() }
    }
    throw 'Too many download redirects.'
}

function Expand-InstallerZip([string]$Archive, [string]$Destination, [long]$MaximumExpandedBytes = 268435456) {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [IO.Compression.ZipFile]::OpenRead($Archive)
    try {
        if ($zip.Entries.Count -eq 0 -or $zip.Entries.Count -gt 4096) { throw 'Invalid archive entry count.' }
        $root = [IO.Path]::GetFullPath($Destination).TrimEnd('\') + '\'
        $seen = New-Object 'Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
        [long]$expanded = 0
        foreach ($entry in $zip.Entries) {
            $name = $entry.FullName
            $parts = $name.TrimEnd('/').Split('/')
            if ($name.StartsWith('/') -or $name.Contains('\') -or $name -match '[\x00-\x1f]' -or
                -not $seen.Add($name.TrimEnd('/').Normalize())) { throw "Unsafe or duplicate archive path: $name" }
            foreach ($part in $parts) {
                if (-not $part -or $part -in @('.','..') -or $part.Contains(':') -or $part.EndsWith('.') -or $part.EndsWith(' ') -or
                    $part -match '^(CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(\.|$)') { throw "Unsafe archive path: $name" }
            }
            $kind = ($entry.ExternalAttributes -shr 16) -band 61440
            if ($kind -notin @(0,16384,32768)) { throw 'Archive contains a link or special file.' }
            $target = [IO.Path]::GetFullPath((Join-Path $Destination $name))
            if (-not $target.StartsWith($root,[StringComparison]::OrdinalIgnoreCase)) { throw 'Archive path escapes its destination.' }
            $expanded += [long]$entry.Length
            if ($expanded -gt $MaximumExpandedBytes) { throw 'Expanded archive exceeds its size limit.' }
        }
        foreach ($entry in $zip.Entries) {
            $target = Join-Path $Destination $entry.FullName
            if ($entry.FullName.EndsWith('/')) {
                [IO.Directory]::CreateDirectory($target) | Out-Null
            } else {
                [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($target)) | Out-Null
                [IO.Compression.ZipFileExtensions]::ExtractToFile($entry,$target,$false)
            }
        }
    } finally { $zip.Dispose() }
}

function Assert-InstallerManifest([string]$Directory, [string]$Version = '') {
    $manifestPath = Join-Path $Directory 'manifest.json'
    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    if ($manifest.version -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$' -or ($Version -and $manifest.version -ne $Version)) {
        throw 'The package version does not match this installer.'
    }
    $root = [IO.Path]::GetFullPath($Directory).TrimEnd('\') + '\'
    foreach ($property in $manifest.files.PSObject.Properties) {
        $name = $property.Name
        if ([IO.Path]::IsPathRooted($name) -or $name -match '(^|[\\/])\.\.([\\/]|$)' -or $name.Contains(':')) {
            throw 'The package manifest contains an unsafe path.'
        }
        $target = [IO.Path]::GetFullPath((Join-Path $Directory $name))
        if (-not $target.StartsWith($root,[StringComparison]::OrdinalIgnoreCase)) { throw 'Manifest path escapes its package.' }
        Assert-InstallerHash $target ([string]$property.Value)
    }
    foreach ($required in @('install.ps1','installer-common.ps1',"painter_mcp-$($manifest.version)-py3-none-any.whl")) {
        if (-not $manifest.files.PSObject.Properties[$required]) { throw "Incomplete package: $required" }
    }
    return $manifest
}

function Remove-InstallerTemp([string]$Path) {
    $resolved = [IO.Path]::GetFullPath($Path)
    $temporaryRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    if (-not $resolved.StartsWith($temporaryRoot,[StringComparison]::OrdinalIgnoreCase) -or
        [IO.Path]::GetFileName($resolved) -notlike 'painter-mcp-installer-*') { throw 'Refusing cleanup outside the installer temporary directory.' }
    if (Test-Path -LiteralPath $resolved) { Remove-Item -LiteralPath $resolved -Recurse -Force -ErrorAction SilentlyContinue }
}

function Test-InstallerPython([string]$Path) {
    if (-not $Path -or -not (Test-Path -LiteralPath $Path -PathType Leaf) -or $Path -like '*\Microsoft\WindowsApps\*') { return $false }
    try {
        & $Path -I -c 'import sys,struct,venv,ensurepip; assert sys.version_info >= (3,10) and struct.calcsize(chr(80))==8' *> $null
        return $LASTEXITCODE -eq 0
    } catch { return $false }
}

function Get-InstallerPython([string]$StateRoot, [switch]$ForcePrivate) {
    if (-not $ForcePrivate) {
        $existing = Join-Path $StateRoot 'venv\Scripts\python.exe'
        if (Test-InstallerPython $existing) { return $existing }
        foreach ($candidate in @(Get-Command python.exe -CommandType Application -All -ErrorAction SilentlyContinue)) {
            if (Test-InstallerPython $candidate.Source) { return $candidate.Source }
        }
        $launcher = Get-Command py.exe -CommandType Application -ErrorAction SilentlyContinue
        if ($launcher) {
            $candidate = & $launcher.Source -3 -c 'import sys; print(sys.executable)' 2>$null
            if ($LASTEXITCODE -eq 0 -and (Test-InstallerPython ([string]$candidate))) { return [string]$candidate }
        }
    }
    # Official CPython NuGet distribution: private and side-by-side; no registry/PATH changes.
    $runtimeVersion = '3.13.15'
    $runtimeHash = '05357887df50d3153efc681bdf432c321d3e2f9ce5788f99f4515b27e8fda0ac'
    $runtime = Join-Path $StateRoot "runtimes\python-$runtimeVersion"
    $python = Join-Path $runtime 'tools\python.exe'
    if (Test-Path -LiteralPath $runtime) {
        if ((Test-InstallerPython $python) -and (Test-Path -LiteralPath (Join-Path $runtime 'painter-runtime.sha256')) -and
            (Get-Content -LiteralPath (Join-Path $runtime 'painter-runtime.sha256') -Raw).Trim() -eq $runtimeHash) { return $python }
        throw "The private Python folder is incomplete. Rename it and rerun setup: $runtime"
    }
    Write-Host "Installing a verified private Python $runtimeVersion runtime (no administrator access)..."
    $temporary = Join-Path ([IO.Path]::GetTempPath()) ('painter-mcp-installer-' + [Guid]::NewGuid().ToString('N'))
    [IO.Directory]::CreateDirectory($temporary) | Out-Null
    try {
        $archive = Join-Path $temporary 'python.nupkg'
        if ($env:PAINTER_MCP_INSTALLER_PYTHON_ARCHIVE) {
            Copy-Item -LiteralPath $env:PAINTER_MCP_INSTALLER_PYTHON_ARCHIVE -Destination $archive
        } else {
            Save-InstallerDownload "https://api.nuget.org/v3-flatcontainer/python/$runtimeVersion/python.$runtimeVersion.nupkg" $archive 67108864
        }
        Assert-InstallerHash $archive $runtimeHash
        $unpacked = Join-Path $temporary 'runtime'
        Expand-InstallerZip $archive $unpacked
        if (-not (Test-InstallerPython (Join-Path $unpacked 'tools\python.exe'))) { throw 'The private Python runtime could not start.' }
        [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($runtime)) | Out-Null
        $sourceFull = [IO.Path]::GetFullPath($unpacked)
        $targetFull = [IO.Path]::GetFullPath($runtime)
        if (-not $sourceFull.StartsWith([IO.Path]::GetFullPath($temporary).TrimEnd('\') + '\',[StringComparison]::OrdinalIgnoreCase) -or
            -not $targetFull.StartsWith([IO.Path]::GetFullPath($StateRoot).TrimEnd('\') + '\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Invalid private runtime destination.' }
        Copy-Item -LiteralPath $unpacked -Destination $runtime -Recurse
        Set-Content -LiteralPath (Join-Path $runtime 'painter-runtime.sha256') -Value $runtimeHash -Encoding ASCII
        return $python
    } finally { Remove-InstallerTemp $temporary }
}
