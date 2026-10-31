# Installs (or removes with -Remove) the logon task that starts AirTrackpad hidden.
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\install_autostart.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\install_autostart.ps1 -Remove

[CmdletBinding()]
param(
    [switch]$Remove
)

$ErrorActionPreference = 'Continue'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }

$ToolsDir = $PSScriptRoot
$AppDir = Split-Path -Parent $ToolsDir
$TaskName = 'AirTrackpad Autostart'
$VbsPath = Join-Path $ToolsDir 'run_hidden.vbs'

if ($Remove) {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $task) {
        Write-Host ('Задача "' + $TaskName + '" не найдена — автозапуск уже выключен.') -ForegroundColor DarkGray
        exit 0
    }
    if ($task.State -eq 'Running') { Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue }
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction Stop
    Write-Host ('Автозапуск удалён: задача "' + $TaskName + '" снята.') -ForegroundColor Green
    Write-Host 'Уже запущенный сервер продолжит работать до tools\stop.ps1 или выхода из Windows.'
    exit 0
}

if (-not (Test-Path -LiteralPath $VbsPath)) {
    Write-Host ('[X] Не найден ' + $VbsPath) -ForegroundColor Red
    exit 1
}

$action = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument ('"' + $VbsPath + '"')
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit ([TimeSpan]::Zero)

try {
    Register-ScheduledTask -TaskName $TaskName `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Description 'AirTrackpad: скрытый автозапуск сервера трекпада при входе в Windows (с перезапуском при падении).' `
        -Force -ErrorAction Stop | Out-Null
} catch {
    Write-Host ('[X] Не удалось создать задачу: ' + $_.Exception.Message) -ForegroundColor Red
    Write-Host '    Попробуйте запустить этот файл от имени администратора.' -ForegroundColor Yellow
    exit 1
}

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task) {
    Write-Host ('[+] Автозапуск включён: задача "' + $TaskName + '".') -ForegroundColor Green
    Write-Host ('    При входе в Windows сервер стартует скрыто, лог: ' + (Join-Path $AppDir 'logs')) -ForegroundColor DarkGray
    Write-Host '    Проверить: tools\status.ps1 | Остановить: tools\stop.ps1 | Убрать: этот файл с -Remove'
    exit 0
}

Write-Host '[X] Задача не появилась после регистрации.' -ForegroundColor Red
exit 1
