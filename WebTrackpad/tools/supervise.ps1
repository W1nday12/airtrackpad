# AirTrackpad supervisor.
# Runs server.py and restarts it if it crashes or exits unexpectedly.
# Manual use:  powershell -NoProfile -ExecutionPolicy Bypass -File tools\supervise.ps1
# Autostart use: tools\run_hidden.vbs (task "AirTrackpad Autostart") passes -Hidden.

[CmdletBinding()]
param(
    [switch]$Hidden,          # no console output, log file only (used by autostart)
    [switch]$NoLog,           # do not write logs
    [string]$PythonPath = ""  # explicit interpreter, otherwise .venv -> setup_env -> global python
)

$ErrorActionPreference = 'Continue'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }
$env:PYTHONIOENCODING = 'utf-8'

$ToolsDir = $PSScriptRoot
if (-not $ToolsDir) { $ToolsDir = Split-Path -Parent $MyInvocation.MyCommand.Path }
$AppDir = Split-Path -Parent $ToolsDir
$LogDir = Join-Path $AppDir 'logs'
if (-not (Test-Path -LiteralPath $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}
$script:LogPath = Join-Path $LogDir ('server-' + (Get-Date -Format 'yyyy-MM-dd') + '.log')
$script:StopFlag = Join-Path $LogDir '.stop'

function Write-Raw {
    param([string]$Text)
    if ($NoLog) { return }
    try { Add-Content -LiteralPath $script:LogPath -Value $Text -ErrorAction Stop } catch { }
}

function Say {
    param([string]$Text)
    $line = '[{0}] {1}' -f (Get-Date -Format 'HH:mm:ss'), $Text
    Write-Raw $line
    if (-not $Hidden) { Write-Host $line }
}

function Rotate-LogIfNeeded {
    if (-not (Test-Path -LiteralPath $script:LogPath)) { return }
    try {
        $size = (Get-Item -LiteralPath $script:LogPath -ErrorAction Stop).Length
        if ($size -gt 5MB) {
            Move-Item -LiteralPath $script:LogPath -Destination ($script:LogPath + '.1') -Force -ErrorAction Stop
        }
    } catch { }
}

function Resolve-Python {
    if ($PythonPath -and (Test-Path -LiteralPath $PythonPath)) {
        return (Resolve-Path -LiteralPath $PythonPath).Path
    }
    $venvPy = Join-Path $AppDir '.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $venvPy) { return $venvPy }

    $setup = Join-Path $ToolsDir 'setup_env.ps1'
    if (Test-Path -LiteralPath $setup) {
        Say 'Виртуальное окружение не найдено — создаю через setup_env.ps1'
        if ($Hidden) {
            & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $setup 2>&1 |
                ForEach-Object { Write-Raw ([string]$_) }
        } else {
            & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $setup
        }
        if (Test-Path -LiteralPath $venvPy) { return $venvPy }
    }

    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

# Only one supervisor may run per user session.
$createdNew = $false
try {
    $mutex = New-Object System.Threading.Mutex($true, 'Local\AirTrackpadSupervisor', [ref]$createdNew)
} catch {
    $createdNew = $true
}
if (-not $createdNew) {
    Say 'Супервизор уже запущен — второй экземпляр не нужен.'
    if (-not $Hidden) { Write-Host 'AirTrackpad уже запущен. Откройте tools\status.ps1 или просто закройте это окно.' }
    exit 0
}

if (-not $Hidden) {
    Write-Host ''
    Write-Host '  ===== AirTrackpad =====' -ForegroundColor Cyan
    Write-Host ('  Папка: ' + $AppDir)
    Write-Host '  Сервер перезапускается автоматически, если упадёт.'
    Write-Host '  Закройте это окно или запустите tools\stop.ps1, чтобы остановить.' -ForegroundColor DarkGray
}

$delay = 2
while ($true) {
    if (Test-Path -LiteralPath $script:StopFlag) {
        Remove-Item -LiteralPath $script:StopFlag -Force -ErrorAction SilentlyContinue
        Say 'Получен сигнал остановки — выхожу.'
        break
    }

    Rotate-LogIfNeeded
    $py = Resolve-Python
    if (-not $py) {
        Say '[!] Python не найден. Установите Python 3.10+ или запустите tools\setup_env.ps1.'
        if (-not $Hidden) { Write-Host '[!] Python не найден. Повтор через 15 секунд...' -ForegroundColor Red }
        Start-Sleep -Seconds 15
        continue
    }

    Say ('Запуск: "' + $py + '" server.py')
    $started = Get-Date
    $code = -1

    Push-Location -LiteralPath $AppDir
    try {
        & $py 'server.py' 2>&1 | ForEach-Object {
            $text = [string]$_
            Write-Raw $text
            if (-not $Hidden) { Write-Host $text }
        }
        $code = $LASTEXITCODE
        if ($null -eq $code) { $code = 0 }
    } catch {
        Say ('[!] Не удалось запустить сервер: ' + $_.Exception.Message)
        $code = -1
    } finally {
        Pop-Location
    }

    $ran = [math]::Round(((Get-Date) - $started).TotalSeconds)
    Say ('Сервер завершился: код ' + $code + ', проработал ' + $ran + ' c')

    if ($ran -gt 60) { $delay = 2 }
    if (-not $Hidden) {
        Write-Host ('[!] Сервер остановился (код ' + $code + '). Перезапуск через ' + $delay + ' c...') -ForegroundColor Yellow
        Write-Host '    Ctrl+C — остановить, закрытие окна — тоже.' -ForegroundColor DarkGray
    }
    Start-Sleep -Seconds $delay
    $delay = [Math]::Min($delay * 2, 30)
}

Say 'Супервизор остановлен.'
try { $mutex.ReleaseMutex() } catch { }
try { $mutex.Dispose() } catch { }
