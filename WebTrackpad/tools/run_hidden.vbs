' Starts the AirTrackpad supervisor without a console window.
' Used by the "AirTrackpad Autostart" scheduled task (see install_autostart.ps1).
Option Explicit

Dim fso, shell, baseDir, cmd
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

baseDir = fso.GetParentFolderName(WScript.ScriptFullName)
cmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & baseDir & "\supervise.ps1"" -Hidden"

' 0 = hidden window, False = do not wait for it to finish
shell.Run cmd, 0, False
