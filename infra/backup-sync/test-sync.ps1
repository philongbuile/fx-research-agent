# Native Windows regression checks; no Drive account, network or third-party modules.
$ErrorActionPreference = 'Stop'
$testRoot = Join-Path ([IO.Path]::GetTempPath()) ('fxra-sync-tests-' + [Guid]::NewGuid().ToString('N'))
$local = Join-Path $testRoot 'local'
$cloud = Join-Path $testRoot 'cloud'
$state = Join-Path $testRoot 'state'
[void][IO.Directory]::CreateDirectory($local)
[void][IO.Directory]::CreateDirectory($cloud)
$shell = (Get-Process -Id $PID).Path
$script = Join-Path $PSScriptRoot 'sync.ps1'

function Assert($condition, [string]$message) {
    if (-not $condition) { throw $message }
}
function Fixture([string]$root, [string]$name, [string]$text) {
    $path = Join-Path $root $name
    [void][IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($path))
    [IO.File]::WriteAllText($path, $text)
    [IO.File]::SetLastWriteTimeUtc($path, [DateTime]::UtcNow.AddMinutes(-1))
}
function Run-Sync {
    & $shell -NoProfile -NonInteractive -File $script -LocalRoot $local -CloudRoot $cloud -StateRoot $state
    $code = $LASTEXITCODE
    $result = Get-Content -LiteralPath (Join-Path $state 'status.json') -Raw | ConvertFrom-Json
    return @{ code = $code; result = $result }
}

Fixture $local 'nested\upload.zip' 'fixture upload'
Fixture $cloud 'download.zip' 'fixture download'
Fixture $local 'same.zip' 'same'
Fixture $cloud 'same.zip' 'same'
Fixture $local 'conflict.zip' 'left'
Fixture $cloud 'conflict.zip' 'right'
Fixture $local 'unfinished.partial' 'not ready'
Fixture $local 'recent.zip' 'still fresh'
[IO.File]::SetLastWriteTimeUtc((Join-Path $local 'recent.zip'), [DateTime]::UtcNow)
$first = Run-Sync
Assert ($first.code -eq 1) 'Conflicts must be reported as a failed check.'
Assert ($first.result.uploaded_to_drive_mount -eq 1) 'Missing upload.'
Assert ($first.result.downloaded_from_drive_mount -eq 1) 'Missing download.'
Assert ($first.result.conflicts.Count -eq 1) 'Missing conflict.'
Assert ($first.result.errors.Count -eq 0) 'Unexpected transfer error.'
Assert ([IO.File]::ReadAllText((Join-Path $local 'conflict.zip')) -eq 'left') 'Local conflict overwritten.'
Assert ([IO.File]::ReadAllText((Join-Path $cloud 'conflict.zip')) -eq 'right') 'Cloud conflict overwritten.'
Assert (-not (Test-Path -LiteralPath (Join-Path $cloud 'unfinished.partial'))) 'Partial file copied.'
Assert (-not (Test-Path -LiteralPath (Join-Path $cloud 'recent.zip'))) 'Active file copied.'
$second = Run-Sync
Assert ($second.result.uploaded_to_drive_mount -eq 0 -and $second.result.downloaded_from_drive_mount -eq 0) 'Retry recopied existing files.'
Assert ($second.result.identical -eq 3) 'Retry did not preserve the verified copies.'
$missing = Join-Path $testRoot 'unmounted'
& $shell -NoProfile -NonInteractive -File $script -LocalRoot $local -CloudRoot $missing -StateRoot $state
Assert ($LASTEXITCODE -eq 0) 'Unavailable mount should defer.'
$deferred = Get-Content -LiteralPath (Join-Path $state 'status.json') -Raw | ConvertFrom-Json
Assert ($deferred.status -eq 'waiting_for_existing_folders_or_google_drive') 'Missing mount was not deferred.'
Assert (-not (Test-Path -LiteralPath $missing)) 'Missing cloud folder recreated.'
$invalidArguments = '-NoProfile -NonInteractive -File "{0}" -LocalRoot "{1}" -CloudRoot "{2}" -StateRoot "{3}"' -f $script,$local,(Join-Path $local 'nested'),$state
$invalid = Start-Process -FilePath $shell -ArgumentList $invalidArguments -WindowStyle Hidden -Wait -PassThru -RedirectStandardError (Join-Path $testRoot 'expected-error.txt') -RedirectStandardOutput (Join-Path $testRoot 'expected-output.txt')
Assert ($invalid.ExitCode -ne 0) 'Nested sync roots accepted.'
Write-Output 'Backup sync checks passed: both directions, conflicts, retry, partial/active files, missing mount and nested roots.'
# Keep isolated fixtures for diagnosis; no recursive deletion of backup data.
