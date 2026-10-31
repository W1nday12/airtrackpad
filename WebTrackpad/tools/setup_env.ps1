#requires -Version 5.1
<#
    setup_env.ps1 — подготовка локального окружения AirTrackpad (Windows).

    Что делает:
      1. Создаёт .venv в корне проекта (python -m venv).
      2. Обновляет pip внутри .venv.
      3. Ставит зависимости из requirements.txt.
      4. Проверяет импорт websockets / qrcode / PIL и печатает путь к python.exe.

    Идемпотентность:
      Если .venv уже есть и импорты проходят, скрипт сразу завершается с кодом 0.

    Параметры:
      -Force  — пересоздать .venv с нуля (существующий удаляется).

    Коды выхода:
      0 — окружение готово;
      1 — ошибка (Python не найден, venv не создан, пакеты не установились).

    Совместимость: Windows PowerShell 5.1+ и PowerShell 7+.
#>
[CmdletBinding()]
param(
    [switch]$Force
)

# UTF-8 вывод, чтобы русские сообщения корректно отображались в консоли.
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }

$ErrorActionPreference = 'Stop'

$ProjectDir = Split-Path -Parent $PSScriptRoot
$VenvDir    = Join-Path $ProjectDir '.venv'
$VenvPython = Join-Path $VenvDir 'Scripts\python.exe'
$ReqFile    = Join-Path $ProjectDir 'requirements.txt'

$script:BasePythonExe  = $null
$script:BasePythonArgs = @()

function Write-Step { param([string]$Message) Write-Host "[*] $Message" -ForegroundColor Cyan }
function Write-Ok   { param([string]$Message) Write-Host "[+] $Message" -ForegroundColor Green }
function Write-Warn { param([string]$Message) Write-Host "[!] $Message" -ForegroundColor Yellow }
function Write-Err  { param([string]$Message) Write-Host "[X] $Message" -ForegroundColor Red }

function Find-BasePython {
    # 1) обычный "python" в PATH (в том числе алиас Microsoft Store)
    $cmd = Get-Command python -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($cmd -and $cmd.Source) {
        & $cmd.Source --version 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            $script:BasePythonExe  = $cmd.Source
            $script:BasePythonArgs = @()
            return $true
        }
    }
    # 2) лаунчер "py -3"
    $cmd = Get-Command py -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($cmd -and $cmd.Source) {
        & $cmd.Source -3 --version 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            $script:BasePythonExe  = $cmd.Source
            $script:BasePythonArgs = @('-3')
            return $true
        }
    }
    return $false
}

function Test-VenvImports {
    param([string]$PythonExe)
    & $PythonExe -c "import websockets, qrcode, PIL" 2>&1 | Out-Null
    return ($LASTEXITCODE -eq 0)
}

function Get-VenvVersions {
    param([string]$PythonExe)
    $code = 'import sys, importlib.metadata as m; print(sys.version.split()[0]); print(m.version("websockets")); print(m.version("qrcode")); print(m.version("pillow"))'
    $out = & $PythonExe -c $code 2>&1
    if ($LASTEXITCODE -ne 0) { return @() }
    return @($out | ForEach-Object { $_.ToString().Trim() })
}

function Remove-BrokenVenv {
    Write-Warn 'Найден неполный .venv — пересоздаю его.'
    try {
        Remove-Item -LiteralPath $VenvDir -Recurse -Force
    } catch {
        Write-Err "Не удалось удалить каталог .venv: $($_.Exception.Message)"
        Write-Err 'Закройте программы, которые используют .venv (сервер, отладчик, терминал), и повторите запуск.'
        exit 1
    }
}

function Show-Versions {
    param([object[]]$Versions)
    if ($Versions -and $Versions.Count -ge 4) {
        Write-Host "Версия Python: $($Versions[0])"
        Write-Host "websockets:    $($Versions[1])"
        Write-Host "qrcode:        $($Versions[2])"
        Write-Host "pillow:        $($Versions[3])"
    }
}

# --- заголовок -------------------------------------------------------------
Write-Host ''
Write-Host '=== AirTrackpad: подготовка окружения ===' -ForegroundColor White
Write-Host "Папка проекта: $ProjectDir"
Write-Host "Каталог .venv:  $VenvDir"
Write-Host ''

if (-not (Test-Path -LiteralPath $ReqFile)) {
    Write-Err "Не найден файл зависимостей: $ReqFile"
    exit 1
}

# --- -Force: пересоздать с нуля --------------------------------------------
if ($Force -and (Test-Path -LiteralPath $VenvDir)) {
    Write-Step 'Параметр -Force: удаляю существующий .venv...'
    try {
        Remove-Item -LiteralPath $VenvDir -Recurse -Force
    } catch {
        Write-Err "Не удалось удалить старый .venv: $($_.Exception.Message)"
        Write-Err 'Закройте процессы, использующие .venv, и повторите запуск.'
        exit 1
    }
}

# --- идемпотентность: готовый .venv -> быстрый выход ----------------------
if (Test-Path -LiteralPath $VenvPython) {
    Write-Step 'Окружение .venv уже существует — проверяю установленные пакеты...'
    if (Test-VenvImports -PythonExe $VenvPython) {
        $versions = Get-VenvVersions -PythonExe $VenvPython
        Write-Ok 'Окружение в порядке, переустановка не требуется.'
        Write-Host ''
        Write-Host "Python: $VenvPython"
        Show-Versions -Versions $versions
        Write-Host ''
        Write-Host 'Для принудительного пересоздания: .\tools\setup_env.ps1 -Force'
        exit 0
    }
    Write-Warn 'В .venv не хватает пакетов — доустанавливаю зависимости.'
} elseif (Test-Path -LiteralPath $VenvDir) {
    Remove-BrokenVenv
}

# --- создание venv ---------------------------------------------------------
if (-not (Test-Path -LiteralPath $VenvPython)) {
    if (-not (Find-BasePython)) {
        Write-Err 'Python не найден в PATH. Установите Python 3.10+ с https://python.org'
        Write-Err 'и включите опцию "Add Python to PATH", затем повторите запуск.'
        exit 1
    }

    Write-Step "Создаю .venv через Python: $script:BasePythonExe $($script:BasePythonArgs -join ' ')"
    $venvArgs = @()
    $venvArgs += $script:BasePythonArgs
    $venvArgs += @('-m', 'venv', $VenvDir)
    & $script:BasePythonExe @venvArgs
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $VenvPython)) {
        Write-Err 'Не удалось создать виртуальное окружение .venv.'
        Write-Err 'Проверьте, что Python 3.10+ установлен и поддерживает модуль venv.'
        exit 1
    }
    Write-Ok "Создан интерпретатор: $VenvPython"
}

# --- pip upgrade -----------------------------------------------------------
Write-Step 'Обновляю pip в .venv...'
& $VenvPython -m pip install --disable-pip-version-check --quiet --upgrade pip
if ($LASTEXITCODE -ne 0) {
    Write-Warn 'Не удалось обновить pip — продолжаю с уже установленной версией.'
}

# --- зависимости -----------------------------------------------------------
Write-Step 'Устанавливаю зависимости из requirements.txt...'
& $VenvPython -m pip install --disable-pip-version-check --requirement $ReqFile
if ($LASTEXITCODE -ne 0) {
    Write-Err 'Не удалось установить зависимости из requirements.txt.'
    Write-Err 'Проверьте подключение к интернету и повторите запуск.'
    exit 1
}

# --- проверка --------------------------------------------------------------
Write-Step 'Проверяю импорт websockets / qrcode / PIL...'
if (-not (Test-VenvImports -PythonExe $VenvPython)) {
    Write-Err 'Пакеты установлены, но импорт не проходит.'
    Write-Err "Проверьте вручную: `"$VenvPython`" -c `"import websockets, qrcode, PIL`""
    exit 1
}

$versions = Get-VenvVersions -PythonExe $VenvPython
Write-Ok 'Окружение готово.'
Write-Host ''
Write-Host "Python: $VenvPython"
Show-Versions -Versions $versions
Write-Host ''
Write-Host 'Опционально (музыкальный остров, winrt):'
Write-Host "  `"$VenvPython`" -m pip install -r requirements-media.txt"
Write-Host ''
exit 0
