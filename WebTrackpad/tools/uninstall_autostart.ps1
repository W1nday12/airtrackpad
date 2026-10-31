# Removes the AirTrackpad logon task created by install_autostart.ps1.
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File tools\uninstall_autostart.ps1

[CmdletBinding()]
param()

$ErrorActionPreference = 'Continue'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }

$TaskName = 'AirTrackpad Autostart'
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $task) {
    Write-Host ('Задача "' + $TaskName + '" не найдена — автозапуск уже выключен.') -ForegroundColor DarkGray
    exit 0
}

if ($task.State -eq 'Running') { Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue }
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction Stop
Write-Host ('Автозапуск удалён: задача "' + $TaskName + '" снята.') -ForegroundColor Green
Write-Host 'Чтобы остановить уже запущенный сервер: tools\stop.ps1'
