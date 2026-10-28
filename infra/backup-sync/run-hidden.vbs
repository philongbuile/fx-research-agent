Option Explicit

' WScript has no console. Start PowerShell hidden from its first window,
' wait for completion, and propagate the sync result to Task Scheduler.
Dim shell, files, script, command, argument
Set shell = CreateObject("WScript.Shell")
Set files = CreateObject("Scripting.FileSystemObject")
script = files.BuildPath(files.GetParentFolderName(WScript.ScriptFullName), "sync.ps1")
If Not files.FileExists(script) Then WScript.Quit 2

command = Quoted(shell.ExpandEnvironmentStrings("%SystemRoot%") & "\System32\WindowsPowerShell\v1.0\powershell.exe")
command = command & " -NoProfile -NonInteractive -WindowStyle Hidden -File " & Quoted(script)
For Each argument In WScript.Arguments
    command = command & " " & Quoted(argument)
Next
WScript.Quit shell.Run(command, 0, True)

Function Quoted(value)
    If InStr(value, Chr(34)) > 0 Or InStr(value, vbCr) > 0 Or InStr(value, vbLf) > 0 Then WScript.Quit 2
    Quoted = Chr(34) & value & Chr(34)
End Function
