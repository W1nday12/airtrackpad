# Stops the AirTrackpad server and its supervisor (manual start or autostart).
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File tools\stop.ps1

[CmdletBinding()]
param()

$ErrorActionPreference = 'Continue'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }

$ToolsDir = $PSScriptRoot
$AppDir = Split-Path -Parent $ToolsDir
$LogDir = Join-Path $AppDir 'logs'
if (-not (Test-Path -LiteralPath $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }

Write-Host 'Останавливаю AirTrackpad...' -ForegroundColor Yellow

# 1) Tell a running supervisor to exit instead of restarting the server.
$stopFlag = Join-Path $LogDir '.stop'
New-Item -ItemType File -Path $stopFlag -Force | Out-Null

# 2) Stop the autostart task instance, if it is running.
$task = Get-ScheduledTask -TaskName 'AirTrackpad Autostart' -ErrorAction SilentlyContinue
if ($task -and $task.State -eq 'Running') {
    Stop-ScheduledTask -TaskName 'AirTrackpad Autostart' -ErrorAction SilentlyContinue
}

# 3) Kill the server processes that hold our ports.
$killed = 0
foreach ($port in 5050..5060) {
    $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    foreach ($conn in $conns) {
        $proc = Get-Process -Id $conn.OwningProcess -ErrorAction SilentlyContinue
        if ($proc -and $proc.ProcessName -like 'python*') {
            Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
            Write-Host ('  остановлен сервер pid ' + $proc.Id + ' (порт ' + $port + ')')
            $killed++
        }
    }
}

# 4) Kill leftover supervisor windows.
$supervisors = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and ($_.CommandLine -like '*supervise.ps1*') -and ($_.ProcessId -ne $PID) }
foreach ($s in $supervisors) {
    Stop-Process -Id $s.ProcessId -Force -ErrorAction SilentlyContinue
}

Start-Sleep -Seconds 1
Remove-Item -LiteralPath $stopFlag -Force -ErrorAction SilentlyContinue

if ($killed -eq 0 -and -not $supervisors) {
    Write-Host 'Похоже, сервер уже был остановлен.' -ForegroundColor DarkGray
} else {
    Write-Host 'Готово. Сервер остановлен.' -ForegroundColor Green
}
