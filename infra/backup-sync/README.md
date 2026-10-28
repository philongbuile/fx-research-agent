# Shared backup folder sync (Windows)

`sync.ps1` copies missing backup snapshots between an existing local folder and an existing Google Drive for desktop folder. Use the **same existing cloud folder** as the other computer, rather than adding a new per-computer backup in Drive preferences. Drive desktop handles network transfer; this helper reconciles the two filesystem paths.

The owner's selected setup (2026-10-27) is:

- Local: `D:\Work\codex\gs_learner\Backups`.
- Existing cloud folder: `G:\Other computers\My Laptop\Backups`.
- Runtime helper: `D:\Work\codex\backup-sync-helper\sync.ps1`.
- Task: `fxra-shared-backup-sync`, at interactive user logon and every five minutes, limited privileges, hidden window. Requires Google Drive signed in and mounted.
- Last status: `D:\Work\codex\backup-sync-helper\status.json`.

The scheduled action uses Windows `wscript.exe` and `run-hidden.vbs` to launch
PowerShell with window style 0 from creation. Directly scheduling PowerShell
with `-WindowStyle Hidden` produced a brief console flash on this workstation.
The launcher waits for sync to finish and returns its exit code to Task Scheduler.
It requires the built-in Windows Script Host/VBScript feature; do not enable a
disabled scripting feature or change security policy automatically.

The existing machine-specific runtime copy remains installed; this directory versions its reusable source with explicit path parameters. Future installations should copy the reviewed source into a stable folder and supply all three parameters. Do not store state inside either sync folder or place credentials in task arguments.

## Behavior

- Copy missing files both ways and verify SHA-256 before publishing the final filename.
- Compare existing names by content; report conflicts without overwriting either copy.
- Never propagate deletions. A deleted file can return from the other side. Coordinate retention on both computers before pruning; this helper is not a destructive mirror.
- Wait until a source is at least 30 seconds old and obtain a read lock excluding concurrent writers.
- Skip partial/temporary/lock files, desktop.ini and reparse-point entries. Refuse destination reparse points and overlapping source/cloud/state roots.
- Preserve missing roots as missing; report a deferred run when Drive is unavailable. A failed transfer may leave an ignored `.partial` file.
- Record counters, relative conflicting/error paths and generic outcomes in `status.json`, not backup contents or credentials. Exit 1 for conflicts/errors, 0 for a completed or deferred run.

Sync immutable archive files only. Do not sync a running PostgreSQL/SQLite database. Existing `infra/hindsight/backup.ps1` creates consistent archives; this helper does not replace that backup job, merge two Hindsight databases or automatically restore either server. Use distinct backup filenames if two hosts can create snapshots at the same timestamp.

## Manual run and validation

From the repository root, with the chosen local and already-mounted cloud directories:

```powershell
powershell.exe -NoProfile -NonInteractive -File infra\backup-sync\sync.ps1 `
  -LocalRoot D:\Work\codex\gs_learner\Backups `
  -CloudRoot 'G:\Other computers\My Laptop\Backups' `
  -StateRoot D:\Work\codex\fx-research-agent\artifacts\backup-sync

powershell.exe -NoProfile -NonInteractive -File infra\backup-sync\test-sync.ps1
```

All paths must be dedicated, non-nested directories; only the state directory may be created automatically. Review paths before use. The native tests use isolated synthetic fixtures and require no network/cloud account. A successful local copy into the Drive mount is not independent proof that another computer has downloaded it; inspect Drive's network sync status and the destination computer when verifying end-to-end delivery.

## Install or recreate the scheduled task

Copy `sync.ps1` and `run-hidden.vbs` together to a stable runtime folder, for example `D:\Work\codex\backup-sync-helper`. The following is for a new installation; refuse to replace an unrelated existing task:

```powershell
$taskName = 'fxra-shared-backup-sync'
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    throw 'Task already exists; review it before changing anything.'
}
$arguments = '//B //NoLogo "D:\Work\codex\backup-sync-helper\run-hidden.vbs" -LocalRoot "D:\Work\codex\gs_learner\Backups" -CloudRoot "G:\Other computers\My Laptop\Backups" -StateRoot "D:\Work\codex\backup-sync-helper\state"'
$action = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\wscript.exe" -Argument $arguments -WorkingDirectory "$env:SystemRoot\System32"
$userSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$logon = New-ScheduledTaskTrigger -AtLogOn -User $userSid
$repeat = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)
$principal = New-ScheduledTaskPrincipal -UserId $userSid -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 4) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger @($logon,$repeat) -Principal $principal -Settings $settings
Start-ScheduledTask -TaskName $taskName
Get-ScheduledTaskInfo -TaskName $taskName
```

Test execution under Task Scheduler, not only in a shell: this environment's initial AppData authoring path was invisible to the scheduled process. The stable D: runtime path worked. No execution-policy or security-setting change is needed on the configured machine.

For an existing reviewed installation, export its task XML privately, copy only
the launcher beside its current script, and use `Set-ScheduledTask -Action` to
replace only its action. Preserve triggers, principal, settings, paths and the
installed sync script. Carry any existing sync path arguments to the launcher;
omit them if the existing action used the installed script's defaults. Start
the task and verify a fresh `status.json` and `LastTaskResult = 0`. Restore the
saved action if the launcher cannot execute. Do not use `cmd.exe` or
`cscript.exe` as the scheduled host, since those are console applications.

Pause with `Disable-ScheduledTask -TaskName fxra-shared-backup-sync`; resume with `Enable-ScheduledTask` and `Start-ScheduledTask` using that name. Preserve the independent Hindsight backup job and all archive files.
