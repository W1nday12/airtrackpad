<#
.SYNOPSIS
    Register the two maintenance scheduled tasks for the current user.

.DESCRIPTION
    Tasks (created with schtasks.exe /create /f, no administrator rights needed):
      * "AirTrackpad Backup"         : daily 23:30  -> backup.ps1
      * "AirTrackpad Cleanup Report" : weekly Sunday 12:00 -> cleanup.ps1 (DryRun, report only!)

    The cleanup task deliberately runs WITHOUT -Apply, so scheduled cleanup only
    writes a report to the log and never deletes or moves anything. Manual
    execution of "cleanup.ps1 -Apply" stays a human decision.

    The task action is:
        powershell.exe -NoProfile -ExecutionPolicy Bypass -File "<ToolsDir>\<script>"

    The command line is passed to schtasks.exe through a ProcessStartInfo with an
    explicitly quoted argument string, so paths containing spaces are safe.

    Both tasks are created "run only when the user is logged on" (default
    schtasks behaviour for a non-admin current-user task).

.PARAMETER ToolsDir
    Folder with backup.ps1 / cleanup.ps1. Default: the folder of this script.

.PARAMETER BackupTime
    Daily backup time. Default: 23:30.

.PARAMETER CleanupTime
    Weekly cleanup report time. Default: 12:00.

.PARAMETER CleanupDay
    Weekly cleanup day. Default: SUN.

.PARAMETER LogDir
    Log folder. Default: <project>\logs (D:\Download\project\logs).

.EXAMPLE
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install_maintenance.ps1

.NOTES
    Windows PowerShell 5.1 compatible. Requires no administrator rights.
#>
[CmdletBinding()]
param(
    [string]$ToolsDir = $PSScriptRoot,
    [string]$BackupTime = '23:30',
    [string]$CleanupTime = '12:00',
    [string]$CleanupDay = 'SUN',
    [string]$LogDir
)

$ErrorActionPreference = 'Continue'
$script:errors = 0

if (-not $ToolsDir) { $ToolsDir = 'D:\Download\project\tools' }
$ToolsDir = (Get-Item -LiteralPath $ToolsDir -Force).FullName.TrimEnd('\')
if (-not $LogDir) { $LogDir = Join-Path (Split-Path -LiteralPath $ToolsDir -Parent) 'logs' }
if (-not (Test-Path -LiteralPath $LogDir -PathType Container)) {
    try { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null } catch { }
}
$logFile = Join-Path $LogDir ('maintenance-install-' + (Get-Date -Format 'yyyyMMdd') + '.log')

function Write-Log {
    param([string]$Message, [string]$Level = 'INFO')
    $line = '{0} [{1}] {2}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Level, $Message
    Write-Host $line
    try { Add-Content -LiteralPath $logFile -Value $line -Encoding UTF8 -ErrorAction Stop } catch { }
}

function Invoke-Schtasks {
    param([Parameter(Mandatory = $true)][string]$Arguments)
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = Join-Path $env:SystemRoot 'System32\schtasks.exe'
    $psi.Arguments = $Arguments
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true
    $process = [System.Diagnostics.Process]::Start($psi)
    $stdout = $process.StandardOutput.ReadToEnd()
    $stderr = $process.StandardError.ReadToEnd()
    $process.WaitForExit()
    $text = ($stdout + $stderr).Trim()
    return [pscustomobject]@{ ExitCode = $process.ExitCode; Text = $text }
}

function New-TaskCommand {
    param([string]$ScriptPath)
    # full command line stored in the task; the quoted path survives spaces
    return 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{0}"' -f $ScriptPath
}

# --------------------------------------------------------------------------- validate
$backupScript = Join-Path $ToolsDir 'backup.ps1'
$cleanupScript = Join-Path $ToolsDir 'cleanup.ps1'
$missing = @()
foreach ($s in @($backupScript, $cleanupScript)) {
    if (-not (Test-Path -LiteralPath $s -PathType Leaf)) { $missing += $s }
}
if ($missing.Count -gt 0) {
    Write-Log ('ERROR: required script(s) not found: {0}' -f ($missing -join ', ')) 'ERROR'
    exit 1
}

Write-Log ('=' * 78)
Write-Log ('install_maintenance start | toolsDir={0}' -f $ToolsDir)
Write-Log ('backup: daily {0} | cleanup report: weekly {1} {2}' -f $BackupTime, $CleanupDay, $CleanupTime)

$backupName = 'AirTrackpad Backup'
$cleanupName = 'AirTrackpad Cleanup Report'

# schtasks strips quotes the way CommandLineToArgvW does, so inner quotes of the
# /tr value must be escaped as \" while the whole value stays inside "...".
$backupCmd = New-TaskCommand -ScriptPath $backupScript
$cleanupCmd = New-TaskCommand -ScriptPath $cleanupScript

$tasks = @(
    [pscustomobject]@{
        Name = $backupName
        Arguments = ('/create /f /tn "{0}" /tr "{1}" /sc DAILY /st {2}' -f $backupName, ($backupCmd -replace '"', '\"'), $BackupTime)
    },
    [pscustomobject]@{
        Name = $cleanupName
        Arguments = ('/create /f /tn "{0}" /tr "{1}" /sc WEEKLY /d {2} /st {3}' -f $cleanupName, ($cleanupCmd -replace '"', '\"'), $CleanupDay, $CleanupTime)
    }
)

foreach ($task in $tasks) {
    Write-Log ('creating task: {0}' -f $task.Name)
    $result = Invoke-Schtasks -Arguments $task.Arguments
    Write-Log ('schtasks exit={0} : {1}' -f $result.ExitCode, $result.Text)
    if ($result.ExitCode -ne 0) { $script:errors++ }
}

# --------------------------------------------------------------------------- verify
Write-Log 'verification:'
foreach ($task in $tasks) {
    $registered = $null
    try {
        $registered = Get-ScheduledTask -TaskName $task.Name -ErrorAction Stop
    } catch {
        $registered = $null
    }
    if (-not $registered) {
        $script:errors++
        Write-Log ('MISSING in Task Scheduler: {0}' -f $task.Name) 'ERROR'
        continue
    }
    $action = $registered.Actions | Select-Object -First 1
    $info = Get-ScheduledTaskInfo -TaskName $task.Name -ErrorAction SilentlyContinue
    $nextRun = ''
    if ($info -and $info.NextRunTime) { $nextRun = $info.NextRunTime.ToString('yyyy-MM-dd HH:mm') }
    Write-Log ('VERIFIED task="{0}" state={1} nextRun={2}' -f $registered.TaskName, $registered.State, $nextRun)
    Write-Log ('   action: {0} {1}' -f $action.Execute, $action.Arguments)
}

if ($script:errors -gt 0) {
    Write-Log ('install_maintenance finished with {0} error(s)' -f $script:errors) 'ERROR'
    exit 1
}
Write-Log 'install_maintenance OK: both tasks are registered (cleanup task runs in DryRun mode)'
exit 0
