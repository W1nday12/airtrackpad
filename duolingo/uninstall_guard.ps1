# Removes the daily "Duolingo Streak Guard" scheduled task.
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File uninstall_guard.ps1

[CmdletBinding()]
param()

$ErrorActionPreference = 'Continue'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }

$TaskName = 'Duolingo Streak Guard'
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $task) {
    Write-Host 'Задача "Duolingo Streak Guard" не найдена — страж уже выключен.' -ForegroundColor DarkGray
    exit 0
}
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction Stop
Write-Host 'Страж выключен: задача "Duolingo Streak Guard" удалена.' -ForegroundColor Green
