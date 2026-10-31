<#
.SYNOPSIS
    Remove the maintenance scheduled tasks created by install_maintenance.ps1.

.DESCRIPTION
    Deletes:
      * "AirTrackpad Backup"
      * "AirTrackpad Cleanup Report"

    Uses schtasks.exe /delete /f (no administrator rights needed). Missing tasks
    are treated as already removed, not as an error.

.PARAMETER LogDir
    Log folder. Default: D:\Download\project\logs.

.EXAMPLE
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\uninstall_maintenance.ps1

.NOTES
    Windows PowerShell 5.1 compatible. Requires no administrator rights.
#>
[CmdletBinding()]
param(
    [string]$LogDir = 'D:\Download\project\logs'
)

$ErrorActionPreference = 'Continue'
$script:errors = 0
$script:removed = 0

if (-not (Test-Path -LiteralPath $LogDir -PathType Container)) {
    try { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null } catch { }
}
$logFile = Join-Path $LogDir ('maintenance-uninstall-' + (Get-Date -Format 'yyyyMMdd') + '.log')

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

$taskNames = @('AirTrackpad Backup', 'AirTrackpad Cleanup Report')

Write-Log ('=' * 78)
Write-Log 'uninstall_maintenance start'

foreach ($name in $taskNames) {
    $exists = $false
    try {
        $null = Get-ScheduledTask -TaskName $name -ErrorAction Stop
        $exists = $true
    } catch {
        $exists = $false
    }
    if (-not $exists) {
        Write-Log ('task "{0}" is not registered - nothing to delete' -f $name) 'WARN'
        continue
    }
    $result = Invoke-Schtasks -Arguments ('/delete /f /tn "{0}"' -f $name)
    Write-Log ('schtasks delete "{0}" exit={1} : {2}' -f $name, $result.ExitCode, $result.Text)
    if ($result.ExitCode -eq 0) {
        $script:removed++
    } else {
        $script:errors++
    }
}

# --------------------------------------------------------------------------- verify
foreach ($name in $taskNames) {
    $still = $null
    try { $still = Get-ScheduledTask -TaskName $name -ErrorAction Stop } catch { $still = $null }
    if ($still) {
        $script:errors++
        Write-Log ('STILL PRESENT: {0}' -f $name) 'ERROR'
    } else {
        Write-Log ('verified removed: {0}' -f $name)
    }
}

if ($script:errors -gt 0) {
    Write-Log ('uninstall_maintenance finished with {0} error(s)' -f $script:errors) 'ERROR'
    exit 1
}
Write-Log ('uninstall_maintenance OK: removed {0} task(s)' -f $script:removed)
exit 0
