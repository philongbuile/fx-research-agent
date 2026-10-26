# Back up the Hindsight server: one consistent zip via hindsight-admin, checked, optionally
# copied off this disk, old files pruned. Windows PowerShell 5.1 compatible (Task Scheduler).
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File infra\hindsight\backup.ps1 [-CopyTo <dir>]
#
# -CopyTo defaults to the FXRA_BACKUP_COPY_TO environment variable. Empty = local only.
# Exit code 0 = backup written and verified; anything else = failure (see backups\backup.log).
param(
    [string]$CopyTo = $env:FXRA_BACKUP_COPY_TO,
    [int]$KeepDays = 14,        # local copies
    [int]$CopyKeepDays = 90,    # off-disk copies
    [int]$KeepMin = 3           # never prune below this many newest files
)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$backups = Join-Path $PSScriptRoot 'backups'
New-Item -ItemType Directory -Force $backups | Out-Null
$log = Join-Path $backups 'backup.log'
function Log($msg) { Add-Content -Path $log -Value ("{0:u} {1}" -f (Get-Date).ToUniversalTime(), $msg) }

function Prune($dir, $days) {
    $files = Get-ChildItem $dir -Filter 'hindsight-*.zip' | Sort-Object LastWriteTime -Descending
    $files | Select-Object -Skip $KeepMin | Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-$days) } |
        ForEach-Object { Remove-Item $_.FullName; Log "pruned $($_.FullName)" }
}

try {
    $name = 'hindsight-{0:yyyyMMddTHHmmssZ}.zip' -f (Get-Date).ToUniversalTime()
    $running = docker compose ps --status running -q hindsight
    if ($LASTEXITCODE -ne 0 -or -not $running) { throw 'hindsight container is not running (is Docker Desktop started?)' }

    docker compose exec -T hindsight hindsight-admin backup "/backups/$name" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "hindsight-admin backup exited $LASTEXITCODE" }

    $file = Join-Path $backups $name
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [System.IO.Compression.ZipFile]::OpenRead($file)   # throws if missing or corrupt
    $entries = $zip.Entries.Count; $zip.Dispose()
    if ($entries -lt 1) { throw "$name is empty" }
    $hash = (Get-FileHash $file -Algorithm SHA256).Hash
    Log "ok $name entries=$entries sha256=$hash"

    if ($CopyTo) {
        New-Item -ItemType Directory -Force $CopyTo | Out-Null
        Copy-Item $file $CopyTo
        if ((Get-FileHash (Join-Path $CopyTo $name) -Algorithm SHA256).Hash -ne $hash) { throw "copy to $CopyTo does not match" }
        Log "copied $name to $CopyTo"
        Prune $CopyTo $CopyKeepDays
    }
    Prune $backups $KeepDays
    exit 0
} catch {
    Log "FAILED $($_.Exception.Message)"
    Write-Error $_.Exception.Message
    exit 1
}
