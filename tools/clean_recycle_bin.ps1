<#
.SYNOPSIS
    Empty the D: Recycle Bin - only on explicit -Confirm, refuses otherwise.

.DESCRIPTION
    Safety tool for the D:\Download cleanup. It is the ONLY component that
    permanently deletes data, therefore it never runs automatically and never
    runs by accident:

      * without -Confirm : prints a warning and exits (code 1), deletes nothing;
      * with    -Confirm : calls Clear-RecycleBin -DriveLetter <DL> -Force.

    The cleanup tasks created by install_maintenance.ps1 never call this script.

.PARAMETER Confirm
    Required to actually empty the Recycle Bin.

.PARAMETER DriveLetter
    Drive whose Recycle Bin is emptied. Default: D.

.PARAMETER LogDir
    Log folder. Default: D:\Download\project\logs.

.EXAMPLE
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\clean_recycle_bin.ps1
    # only shows the warning

.EXAMPLE
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\clean_recycle_bin.ps1 -Confirm
    # REALLY empties the D: Recycle Bin

.NOTES
    Windows PowerShell 5.1 compatible. Requires no administrator rights.
#>
[CmdletBinding()]
param(
    [switch]$Confirm,
    [ValidatePattern('^[A-Za-z]$')][string]$DriveLetter = 'D',
    [string]$LogDir = 'D:\Download\project\logs'
)

$ErrorActionPreference = 'Continue'
$drive = $DriveLetter.ToUpperInvariant()

if (-not (Test-Path -LiteralPath $LogDir -PathType Container)) {
    try { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null } catch { }
}
$logFile = Join-Path $LogDir ('recycle-' + (Get-Date -Format 'yyyyMMdd') + '.log')

function Write-Log {
    param([string]$Message, [string]$Level = 'INFO')
    $line = '{0} [{1}] {2}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Level, $Message
    Write-Host $line
    try { Add-Content -LiteralPath $logFile -Value $line -Encoding UTF8 -ErrorAction Stop } catch { }
}

Write-Log ('=' * 78)
Write-Log ('clean_recycle_bin | drive={0} | confirm={1}' -f $drive, [bool]$Confirm)

if (-not $Confirm) {
    Write-Host ''
    Write-Host '======================================================================' -ForegroundColor Yellow
    Write-Host (' REFUSED: emptying the Recycle Bin of drive {0}: requires -Confirm.' -f $drive) -ForegroundColor Yellow
    Write-Host ' This operation is IRREVERSIBLE: files will NOT go to the Recycle Bin,' -ForegroundColor Yellow
    Write-Host ' they will be deleted permanently.' -ForegroundColor Yellow
    Write-Host ' The cleanup.ps1 script never calls this tool automatically.' -ForegroundColor Yellow
    Write-Host '' -ForegroundColor Yellow
    Write-Host (' To really empty it:  .\clean_recycle_bin.ps1 -Confirm' -f $drive) -ForegroundColor Yellow
    Write-Host '======================================================================' -ForegroundColor Yellow
    Write-Host ''
    Write-Log 'refused: -Confirm was not specified, nothing was deleted' 'WARN'
    exit 1
}

Write-Log ('-Confirm specified: calling Clear-RecycleBin -DriveLetter {0} -Force' -f $drive) 'WARN'
try {
    $currentDrive = Get-PSDrive -Name $drive -ErrorAction Stop
    Write-Log ('drive {0}: found (used {1:N2} GB, free {2:N2} GB)' -f $drive, ($currentDrive.Used / 1GB), ($currentDrive.Free / 1GB))
} catch {
    Write-Log ('drive {0}: is not available ({1})' -f $drive, $_.Exception.Message) 'WARN'
}

try {
    Clear-RecycleBin -DriveLetter $drive -Force -ErrorAction Stop
    Write-Log ('SUCCESS: Recycle Bin of drive {0} was emptied' -f $drive)
    exit 0
} catch {
    Write-Log ('ERROR: Clear-RecycleBin failed for drive {0}: {1}' -f $drive, $_.Exception.Message) 'ERROR'
    exit 1
}
