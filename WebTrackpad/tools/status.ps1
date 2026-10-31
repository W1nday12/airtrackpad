# AirTrackpad status: is the server running, on which port, which URLs to open,
# firewall state, autostart state and the tail of the log.
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\status.ps1
#   ... -Qr            also print an ASCII QR code for the phone
#   ... -EnsureRunning start the server (via the autostart task) if it is down

[CmdletBinding()]
param(
    [switch]$Qr,
    [switch]$EnsureRunning,
    [int]$Tail = 10
)

$ErrorActionPreference = 'Continue'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }
$env:PYTHONIOENCODING = 'utf-8'

$ToolsDir = $PSScriptRoot
$AppDir = Split-Path -Parent $ToolsDir
$TaskName = 'AirTrackpad Autostart'
$Ports = 5050..5060

function Get-VenvPython {
    $venvPy = Join-Path $AppDir '.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $venvPy) { return $venvPy }
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

function Get-ServerListener {
    Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
        Where-Object { ($Ports -contains $_.LocalPort) -and ($_.LocalAddress -eq '0.0.0.0') } |
        Sort-Object LocalPort |
        Select-Object -First 1
}

# Python helpers must run from the project folder: -c puts the current
# directory on sys.path, and network_info lives next to server.py.
function Invoke-Python {
    param([string]$Code, [string]$Arg = '')
    if (-not $py) { return $null }
    Push-Location -LiteralPath $AppDir
    try {
        if ($Arg) { & $py -c $Code $Arg 2>$null } else { & $py -c $Code 2>$null }
    } finally {
        Pop-Location
    }
}

Write-Host ''
Write-Host '  ===== AirTrackpad: статус =====' -ForegroundColor Cyan
Write-Host ('  Папка: ' + $AppDir)

$listener = Get-ServerListener
$py = Get-VenvPython

if ($listener) {
    $proc = Get-Process -Id $listener.OwningProcess -ErrorAction SilentlyContinue
    $procName = '?'
    if ($proc) { $procName = $proc.ProcessName }
    Write-Host ('  Сервер:  РАБОТАЕТ  (порт ' + $listener.LocalPort + ', pid ' + $listener.OwningProcess + ', ' + $procName + ')') -ForegroundColor Green

    if ($py) {
        $json = Invoke-Python -Code 'import json,sys,network_info;print(json.dumps(network_info.get_url_options(int(sys.argv[1])),ensure_ascii=False))' -Arg ([string]$listener.LocalPort)
        $urls = $null
        try { $urls = $json | ConvertFrom-Json } catch { }
        if ($urls) {
            Write-Host '  Адреса для телефона:'
            foreach ($u in $urls) { Write-Host ('    ' + $u.url + '   (' + $u.label + ')') }
            $best = $urls[0].url
        } else {
            $best = 'http://127.0.0.1:' + $listener.LocalPort + '/'
            Write-Host ('    ' + $best)
        }
        if ($Qr) {
            Write-Host '  QR-код (наведите камеру телефона):'
            Invoke-Python -Code 'import sys,qrcode;q=qrcode.QRCode(border=1);q.add_data(sys.argv[1]);q.print_ascii(invert=True)' -Arg $best |
                ForEach-Object { Write-Host $_ }
        }
    }
} else {
    Write-Host '  Сервер:  НЕ РАБОТАЕТ' -ForegroundColor Yellow
    if ($EnsureRunning) {
        $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        if ($task) {
            Write-Host '  Запускаю через задачу автозапуска...'
            Start-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 6
            $listener = Get-ServerListener
            if ($listener) {
                Write-Host ('  Сервер запущен на порту ' + $listener.LocalPort) -ForegroundColor Green
            } else {
                Write-Host '  Не поднялся за 6 секунд — смотрите лог ниже.' -ForegroundColor Yellow
            }
        } else {
            Write-Host ('  Задача "' + $TaskName + '" не установлена. Запустите tools\install_autostart.ps1') -ForegroundColor Yellow
        }
    } else {
        Write-Host '  Подсказка: запустите «Запустить Трекпад.bat» или status.ps1 -EnsureRunning'
    }
}

$fw = Get-NetFirewallRule -DisplayName 'AirTrackpad' -ErrorAction SilentlyContinue
if ($fw) {
    Write-Host '  Брандмауэр: правило для портов 5050-5060 есть.' -ForegroundColor Green
} else {
    Write-Host '  Брандмауэр: правила НЕТ — запустите «Разрешить доступ в брандмауэре.bat».' -ForegroundColor Yellow
}

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task) {
    $state = (Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction SilentlyContinue).LastTaskResult
    Write-Host ('  Автозапуск: включён (задача "' + $TaskName + '", состояние ' + $task.State + ')') -ForegroundColor Green
} else {
    Write-Host '  Автозапуск: выключен (запустите tools\install_autostart.ps1)' -ForegroundColor DarkGray
}

$logPath = Join-Path $AppDir ('logs\server-' + (Get-Date -Format 'yyyy-MM-dd') + '.log')
if (-not (Test-Path -LiteralPath $logPath)) {
    $latest = Get-ChildItem -LiteralPath (Join-Path $AppDir 'logs') -Filter 'server-*.log' -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($latest) { $logPath = $latest.FullName }
}
if (Test-Path -LiteralPath $logPath) {
    Write-Host ''
    Write-Host ('  Последние строки лога (' + (Split-Path -Leaf $logPath) + '):') -ForegroundColor DarkGray
    Get-Content -LiteralPath $logPath -Tail $Tail -Encoding UTF8 -ErrorAction SilentlyContinue |
        ForEach-Object { Write-Host ('    ' + $_) -ForegroundColor DarkGray }
}

Write-Host ''
if ($listener) { exit 0 } else { exit 1 }
