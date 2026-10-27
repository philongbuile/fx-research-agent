param(
    [Parameter(Mandatory = $true)][string]$LocalRoot,
    [Parameter(Mandatory = $true)][string]$CloudRoot,
    [Parameter(Mandatory = $true)][string]$StateRoot
)

# Additive synchronization of immutable backup files. Never delete or overwrite.
# Google Drive for desktop handles transport from the mounted cloud folder.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$LocalRoot = [IO.Path]::GetFullPath($LocalRoot).TrimEnd('\')
$CloudRoot = [IO.Path]::GetFullPath($CloudRoot).TrimEnd('\')
$StateRoot = [IO.Path]::GetFullPath($StateRoot).TrimEnd('\')
foreach ($root in @($LocalRoot, $CloudRoot, $StateRoot)) {
    if ($root -eq [IO.Path]::GetPathRoot($root).TrimEnd('\')) { throw 'Use a dedicated folder, not a drive root.' }
}
foreach ($pair in @(@($LocalRoot, $CloudRoot), @($LocalRoot, $StateRoot), @($CloudRoot, $StateRoot))) {
    if ($pair[0] -eq $pair[1] -or $pair[0].StartsWith($pair[1] + '\', [StringComparison]::OrdinalIgnoreCase) -or
        $pair[1].StartsWith($pair[0] + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Local, cloud and state folders must be separate and non-nested.'
    }
}
[void][IO.Directory]::CreateDirectory($StateRoot)
$status = [ordered]@{
    started_utc = [DateTime]::UtcNow.ToString('o')
    status = 'running'
    uploaded_to_drive_mount = 0
    downloaded_from_drive_mount = 0
    identical = 0
    deferred = 0
    conflicts = @()
    errors = @()
}
$runLock = $null

function Get-BackupFiles([string]$Root) {
    $result = @{}
    $pending = [Collections.Generic.Stack[string]]::new()
    $pending.Push($Root)
    while ($pending.Count -gt 0) {
        foreach ($entry in Get-ChildItem -LiteralPath $pending.Pop() -Force) {
            if ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) { continue }
            if ($entry.PSIsContainer) { $pending.Push($entry.FullName); continue }
            if ($entry.Name -eq 'desktop.ini' -or $entry.Name -match '(?i)\.(tmp|partial|crdownload|lock)$') { continue }
            $relative = $entry.FullName.Substring($Root.Length).TrimStart('\')
            $result[$relative] = $entry
        }
    }
    return ,$result
}

function Get-StreamHash([IO.Stream]$Stream) {
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($sha.ComputeHash($Stream)) }
    finally { $sha.Dispose() }
}

function Copy-NewBackup([IO.FileInfo]$Source, [string]$Root, [string]$Relative) {
    if ($Source.LastWriteTimeUtc -gt [DateTime]::UtcNow.AddSeconds(-30)) { return 'deferred' }
    $destination = [IO.Path]::GetFullPath((Join-Path $Root $Relative))
    if (-not $destination.StartsWith($Root + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Destination escaped the configured backup root.'
    }
    $directory = [IO.Path]::GetDirectoryName($destination)
    $cursor = $directory
    while ($cursor.Length -ge $Root.Length) {
        if ((Test-Path -LiteralPath $cursor) -and
            ((Get-Item -LiteralPath $cursor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw 'Reparse-point destination refused.'
        }
        if ($cursor -eq $Root) { break }
        $cursor = [IO.Path]::GetDirectoryName($cursor)
    }
    [void][IO.Directory]::CreateDirectory($directory)
    $temporary = Join-Path $directory ('.fxra-sync-' + [Guid]::NewGuid().ToString('N') + '.partial')
    $sourceStream = $null
    $targetStream = $null
    try {
        # Deny concurrent writers while copying an already stable source file.
        $sourceStream = [IO.File]::Open($Source.FullName, 'Open', 'Read', 'Read')
        $targetStream = [IO.File]::Open($temporary, 'CreateNew', 'ReadWrite', 'None')
        $sourceStream.CopyTo($targetStream)
        $targetStream.Flush($true)
        $sourceStream.Position = 0
        $targetStream.Position = 0
        if ((Get-StreamHash $sourceStream) -ne (Get-StreamHash $targetStream)) {
            throw 'Copied content failed its integrity check.'
        }
        $targetStream.Dispose(); $targetStream = $null
        # Rename within the target directory; File.Move refuses an existing destination.
        [IO.File]::Move($temporary, $destination)
        return 'copied'
    } finally {
        if ($targetStream) { $targetStream.Dispose() }
        if ($sourceStream) { $sourceStream.Dispose() }
        # A failed transfer may leave an ignored .partial file; never delete user files.
    }
}

try {
    try { $runLock = [IO.File]::Open((Join-Path $StateRoot 'sync.lock'), 'OpenOrCreate', 'ReadWrite', 'None') }
    catch [IO.IOException] { exit 0 }
    if (-not (Test-Path -LiteralPath $LocalRoot -PathType Container) -or
        -not (Test-Path -LiteralPath $CloudRoot -PathType Container)) {
        $status.status = 'waiting_for_existing_folders_or_google_drive'
    } else {
        $local = Get-BackupFiles $LocalRoot
        $cloud = Get-BackupFiles $CloudRoot
        foreach ($relative in @(@($local.Keys) + @($cloud.Keys) | Sort-Object -Unique)) {
            try {
                if ($local.ContainsKey($relative) -and $cloud.ContainsKey($relative)) {
                    if ((Get-FileHash -LiteralPath $local[$relative].FullName -Algorithm SHA256).Hash -eq
                        (Get-FileHash -LiteralPath $cloud[$relative].FullName -Algorithm SHA256).Hash) {
                        $status.identical++
                    } else { $status.conflicts += $relative }
                } elseif ($local.ContainsKey($relative)) {
                    if ((Copy-NewBackup $local[$relative] $CloudRoot $relative) -eq 'copied') {
                        $status.uploaded_to_drive_mount++
                    } else { $status.deferred++ }
                } else {
                    if ((Copy-NewBackup $cloud[$relative] $LocalRoot $relative) -eq 'copied') {
                        $status.downloaded_from_drive_mount++
                    } else { $status.deferred++ }
                }
            } catch {
                # Log relative file names and generic outcomes, never backup contents/credentials.
                $status.errors += $relative
            }
        }
        $status.status = if ($status.errors.Count -or $status.conflicts.Count) { 'needs_attention' } else { 'ok' }
    }
} catch {
    $status.status = 'failed'
    $status.errors += 'Could not complete backup folder synchronization.'
} finally {
    if ($runLock) {
        $status.finished_utc = [DateTime]::UtcNow.ToString('o')
        $status | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $StateRoot 'status.json') -Encoding UTF8
        $runLock.Dispose()
    }
}
if ($status.status -in @('failed', 'needs_attention')) { exit 1 }
