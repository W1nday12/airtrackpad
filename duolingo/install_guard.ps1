# Installs / removes the daily "Duolingo Streak Guard" scheduled task.
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File install_guard.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File install_guard.ps1 -Time 23:50
#   powershell -NoProfile -ExecutionPolicy Bypass -File install_guard.ps1 -Remove

[CmdletBinding()]
param(
    [string]$Time = '23:50',
    [switch]$Remove
)

$ErrorActionPreference = 'Continue'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }

$Dir = $PSScriptRoot
$TaskName = 'Duolingo Streak Guard'
$Script = Join-Path $Dir 'duo_guard.py'
$Config = Join-Path $Dir 'config.json'

if ($Remove) {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $task) {
        Write-Host 'Задача "Duolingo Streak Guard" не найдена — уже выключено.' -ForegroundColor DarkGray
        exit 0
    }
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction Stop
    Write-Host 'Задача "Duolingo Streak Guard" удалена.' -ForegroundColor Green
    exit 0
}

if (-not (Test-Path -LiteralPath $Script)) {
    Write-Host ('[X] Не найден ' + $Script) -ForegroundColor Red
    exit 1
}

# Make sure the token is present before scheduling anything.
$tokenOk = $false
if (Test-Path -LiteralPath $Config) {
    try {
        $cfg = Get-Content -LiteralPath $Config -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($cfg.jwt_token -and $cfg.jwt_token.Trim().Length -gt 40) { $tokenOk = $true }
    } catch { }
}
if (-not $tokenOk) {
    Write-Host '[!] В config.json нет jwt_token — задача установится, но работать не будет.' -ForegroundColor Yellow
    Write-Host '    Вставьте токен в config.json (инструкция в README.md) и запустите проверку:' -ForegroundColor Yellow
    Write-Host '    python duo_guard.py --check' -ForegroundColor DarkGray
}

$pyw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
if (-not $pyw) {
    $py = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
    if (-not $py) {
        Write-Host '[X] Python не найден. Установите Python 3.10+.' -ForegroundColor Red
        exit 1
    }
    $pyw = $py
}

$action = New-ScheduledTaskAction -Execute $pyw -Argument ('"' + $Script + '" --run') -WorkingDirectory $Dir
$trigger = New-ScheduledTaskTrigger -Daily -At $Time
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit ([TimeSpan]::FromMinutes(10))

try {
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Settings $settings `
        -Description 'Duolingo: за 10 минут до конца дня проверяет стрик и при необходимости завершает короткую историю.' `
        -Force -ErrorAction Stop | Out-Null
} catch {
    Write-Host ('[X] Не удалось создать задачу: ' + $_.Exception.Message) -ForegroundColor Red
    exit 1
}

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task) {
    $info = Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction SilentlyContinue
    Write-Host ('[+] Задача "' + $TaskName + '" установлена: каждый день в ' + $Time + '.') -ForegroundColor Green
    Write-Host ('    Интерпретатор: ' + $pyw)
    Write-Host ('    Ближайший запуск: ' + $info.NextRunTime)
    Write-Host '    Проверить вручную: python duo_guard.py --check (безопасно, ничего не делает)'
    Write-Host '    Убрать: install_guard.ps1 -Remove'
    exit 0
}

Write-Host '[X] Задача не появилась после регистрации.' -ForegroundColor Red
exit 1
