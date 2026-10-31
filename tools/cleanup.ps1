<#
.SYNOPSIS
    Safe, reversible cleanup of the D:\Download dump. Report-only (DryRun) by default.

.DESCRIPTION
    Scans ONLY the root of -Root (not recursive). File categories:
      * Documents  (.ods .xlsx .xls .docx .doc .pdf)  -> MOVED to -ArchiveDir (never deleted)
      * Installers/archives (.exe .msi .msix .appx .zip .7z .rar .iso .tar .gz .tgz .cab .nupkg)
                                                     -> Recycle Bin when older than -MinAgeDays
      * Loose junk (.dll .bin .pak .rpf .torrent .sh .tmp ...)
                                                     -> Recycle Bin when older than -MinAgeDays
      * Anything else                                -> listed for manual review, left untouched

    Safety rules (hard-coded):
      * DryRun by default. -Apply is required to change anything.
      * Never touches <Root>\project or D:\Download\project (and everything below them).
      * Never deletes permanently: deletions go to the Recycle Bin (Microsoft.VisualBasic.FileIO).
      * Never touches items younger than -MinAgeDays (documents use -DocMinAgeDays,
        which defaults to -MinAgeDays). Age = max(LastWriteTime, CreationTime).
      * Never deletes directories. Only root-level FILES are candidates.
      * Hidden/system/reparse-point files are skipped.
      * Full audit log in -LogDir\cleanup-YYYYMMDD.log (one line per decision).
      * Unicode/space-safe paths: -LiteralPath everywhere.

.PARAMETER Apply
    Actually perform the actions. Without it nothing is deleted or moved.

.PARAMETER Root
    Folder to clean. Default: D:\Download.

.PARAMETER ArchiveDir
    Documents destination. Default: D:\Archive\Documents (for the standard Root),
    otherwise <Root>\Archive\Documents.

.PARAMETER LogDir
    Log folder. Default: D:\Download\project\logs (for the standard Root), otherwise <Root>\logs.

.PARAMETER MinAgeDays
    Minimum age for deletion candidates. Default: 30.

.PARAMETER DocMinAgeDays
    Minimum age for document moves. Default: same as -MinAgeDays. Use 0 to archive
    documents regardless of age (the move is reversible, nothing is deleted).

.PARAMETER ExtraProtectedPaths
    Additional paths that must never be touched (e.g. other project folders).

.EXAMPLE
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\cleanup.ps1
    # DryRun report, changes nothing.

.EXAMPLE
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\cleanup.ps1 -Apply
    # Executes: old installers/junk -> Recycle Bin, old documents -> D:\Archive\Documents.

.EXAMPLE
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\cleanup.ps1 -Apply -DocMinAgeDays 0
    # Also archive documents that are newer than 30 days (still never deleted).

.NOTES
    Windows PowerShell 5.1 compatible. Requires no administrator rights.
#>
[CmdletBinding()]
param(
    [switch]$Apply,
    [string]$Root = 'D:\Download',
    [string]$ArchiveDir,
    [string]$LogDir,
    [int]$MinAgeDays = 30,
    [int]$DocMinAgeDays = -1,
    [string[]]$ExtraProtectedPaths = @()
)

$ErrorActionPreference = 'Continue'
$script:errors = 0

# --------------------------------------------------------------------------- paths
if (-not (Test-Path -LiteralPath $Root -PathType Container)) {
    Write-Host ("ERROR: cleanup root not found: {0}" -f $Root) -ForegroundColor Red
    exit 1
}
$rootItem = Get-Item -LiteralPath $Root -Force
$rootFull = $rootItem.FullName.TrimEnd('\')

if (-not $ArchiveDir) {
    if ($rootFull -eq 'D:\Download') {
        $ArchiveDir = 'D:\Archive\Documents'
    } else {
        $ArchiveDir = Join-Path $rootFull 'Archive\Documents'
    }
}
if (-not $LogDir) {
    if ($rootFull -eq 'D:\Download') {
        $LogDir = 'D:\Download\project\logs'
    } else {
        $LogDir = Join-Path $rootFull 'logs'
    }
}
if ($DocMinAgeDays -lt 0) { $DocMinAgeDays = $MinAgeDays }

# protected paths: the project folder is always off limits
$protected = New-Object System.Collections.Generic.List[string]
$protected.Add('D:\Download\project')
$protected.Add((Join-Path $rootFull 'project'))
foreach ($p in $ExtraProtectedPaths) {
    if ($p) { $protected.Add($p) }
}

if (-not (Test-Path -LiteralPath $LogDir -PathType Container)) {
    try {
        New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
    } catch {
        Write-Host ("WARNING: cannot create log dir {0}: {1}" -f $LogDir, $_.Exception.Message) -ForegroundColor Yellow
    }
}
$logFile = Join-Path $LogDir ('cleanup-' + (Get-Date -Format 'yyyyMMdd') + '.log')

# --------------------------------------------------------------------------- helpers
function Write-Log {
    param(
        [Parameter(Mandatory = $true)][string]$Message,
        [string]$Level = 'INFO'
    )
    $line = '{0} [{1}] {2}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Level, $Message
    Write-Host $line
    try {
        Add-Content -LiteralPath $logFile -Value $line -Encoding UTF8 -ErrorAction Stop
    } catch {
        # never fail the run because of the log file
    }
}

function Format-Bytes {
    param([long]$Bytes)
    if ($Bytes -ge 1GB) { return ('{0:N2} GB' -f ($Bytes / 1GB)) }
    if ($Bytes -ge 1MB) { return ('{0:N2} MB' -f ($Bytes / 1MB)) }
    if ($Bytes -ge 1KB) { return ('{0:N2} KB' -f ($Bytes / 1KB)) }
    return ('{0} B' -f $Bytes)
}

function Test-IsProtected {
    param([string]$Path)
    foreach ($p in $protected) {
        $norm = $p.TrimEnd('\')
        if ([string]::Equals($Path, $norm, [System.StringComparison]::OrdinalIgnoreCase)) { return $true }
        if ($Path.StartsWith($norm + '\', [System.StringComparison]::OrdinalIgnoreCase)) { return $true }
    }
    return $false
}

function Get-EffectiveAgeDays {
    param([System.IO.FileInfo]$File)
    # conservative: use the newest of LastWriteTime / CreationTime
    $stamp = $File.LastWriteTime
    if ($File.CreationTime -gt $stamp) { $stamp = $File.CreationTime }
    return ((Get-Date) - $stamp).TotalDays
}

function Get-UniqueFilePath {
    param([string]$Directory, [string]$Name)
    $candidate = Join-Path $Directory $Name
    if (-not (Test-Path -LiteralPath $candidate)) { return $candidate }
    $base = [System.IO.Path]::GetFileNameWithoutExtension($Name)
    $ext = [System.IO.Path]::GetExtension($Name)
    $i = 1
    while ($true) {
        $candidate = Join-Path $Directory ('{0} ({1}){2}' -f $base, $i, $ext)
        if (-not (Test-Path -LiteralPath $candidate)) { return $candidate }
        $i++
    }
}

# --------------------------------------------------------------------------- classification
$docExtensions = @('.ods', '.xlsx', '.xls', '.docx', '.doc', '.pdf')
$installerExtensions = @('.exe', '.msi', '.msix', '.msixbundle', '.appx', '.zip', '.7z', '.rar', '.iso', '.cab', '.nupkg', '.tar', '.gz', '.tgz')
$junkExtensions = @('.dll', '.bin', '.pak', '.rpf', '.torrent', '.sh', '.tmp', '.bak', '.old', '.log', '.crdownload', '.part', '.download', '.pdb', '.pyc', '.pyo', '.msi.torrent')

function Get-FileCategory {
    param([System.IO.FileInfo]$File)
    $name = $File.Name.ToLowerInvariant()
    $ext = $File.Extension.ToLowerInvariant()
    if ($docExtensions -contains $ext) { return 'Document' }
    if ($name.EndsWith('.tar.gz') -or $name.EndsWith('.tar.xz')) { return 'Installer' }
    if ($installerExtensions -contains $ext) { return 'Installer' }
    if ($junkExtensions -contains $ext) { return 'Junk' }
    return 'Review'
}

# --------------------------------------------------------------------------- start
$mode = 'DRY-RUN'
if ($Apply) { $mode = 'APPLY' }

Write-Log ('=' * 78)
Write-Log ('cleanup start | mode={0} | root={1}' -f $mode, $rootFull)
Write-Log ('minAgeDays={0} docMinAgeDays={1} archiveDir={2} logFile={3}' -f $MinAgeDays, $DocMinAgeDays, $ArchiveDir, $logFile)
Write-Log ('protected: {0}' -f ($protected -join '; '))
if (-not $Apply) {
    Write-Log 'DRY-RUN: nothing will be deleted or moved. Use -Apply to execute.' 'WARN'
}

if ($Apply) {
    try {
        Add-Type -AssemblyName Microsoft.VisualBasic -ErrorAction Stop
    } catch {
        Write-Log ('ERROR: cannot load Microsoft.VisualBasic assembly: {0}' -f $_.Exception.Message) 'ERROR'
        exit 1
    }
}

$deleteCandidates = New-Object System.Collections.Generic.List[object]
$docCandidates = New-Object System.Collections.Generic.List[object]
$recentSkipped = New-Object System.Collections.Generic.List[object]
$protectedSkipped = New-Object System.Collections.Generic.List[object]
$reviewFiles = New-Object System.Collections.Generic.List[object]
$dirSkipped = New-Object System.Collections.Generic.List[string]
$deleted = New-Object System.Collections.Generic.List[object]
$archived = New-Object System.Collections.Generic.List[object]

# --------------------------------------------------------------------------- scan
$rootItems = @(Get-ChildItem -LiteralPath $rootFull -Force -ErrorAction SilentlyContinue)
foreach ($item in $rootItems) {
    if ($item.PSIsContainer) {
        $dirSkipped.Add($item.Name)
        Write-Log ('[SKIP-DIR] {0} (directories are never cleaned)' -f $item.Name) 'DEBUG'
        continue
    }

    $full = $item.FullName
    $size = [long]$item.Length
    $age = Get-EffectiveAgeDays -File $item

    if (Test-IsProtected -Path $full) {
        $protectedSkipped.Add($item)
        Write-Log ('[SKIP-PROTECTED] {0}' -f $item.Name) 'WARN'
        continue
    }
    $attrs = [string]$item.Attributes
    if ($attrs -match 'Hidden|System|ReparsePoint') {
        $protectedSkipped.Add($item)
        Write-Log ('[SKIP-ATTR] {0} ({1})' -f $item.Name, $attrs) 'WARN'
        continue
    }

    $category = Get-FileCategory -File $item
    $ageText = '{0:N1} d' -f $age
    $sizeText = Format-Bytes $size

    switch ($category) {
        'Document' {
            if ($age -ge $DocMinAgeDays) {
                $docCandidates.Add($item)
                Write-Log ('[ARCHIVE] {0} | {1} | age {2}' -f $item.Name, $sizeText, $ageText) 'PLAN'
            } else {
                $recentSkipped.Add($item)
                Write-Log ('[SKIP-RECENT] {0} | {1} | age {2} < {3} d (document)' -f $item.Name, $sizeText, $ageText, $DocMinAgeDays) 'DEBUG'
            }
        }
        'Installer' {
            if ($age -ge $MinAgeDays) {
                $deleteCandidates.Add($item)
                Write-Log ('[RECYCLE] {0} | {1} | age {2} (installer/archive)' -f $item.Name, $sizeText, $ageText) 'PLAN'
            } else {
                $recentSkipped.Add($item)
                Write-Log ('[SKIP-RECENT] {0} | {1} | age {2} < {3} d (installer)' -f $item.Name, $sizeText, $ageText, $MinAgeDays) 'DEBUG'
            }
        }
        'Junk' {
            if ($age -ge $MinAgeDays) {
                $deleteCandidates.Add($item)
                Write-Log ('[RECYCLE] {0} | {1} | age {2} (loose junk)' -f $item.Name, $sizeText, $ageText) 'PLAN'
            } else {
                $recentSkipped.Add($item)
                Write-Log ('[SKIP-RECENT] {0} | {1} | age {2} < {3} d (junk)' -f $item.Name, $sizeText, $ageText, $MinAgeDays) 'DEBUG'
            }
        }
        default {
            $reviewFiles.Add($item)
            Write-Log ('[REVIEW] {0} | {1} | age {2} | not touched (unknown type)' -f $item.Name, $sizeText, $ageText) 'DEBUG'
        }
    }
}

# --------------------------------------------------------------------------- totals
$bytesToDelete = 0
foreach ($f in $deleteCandidates) { $bytesToDelete += [long]$f.Length }
$bytesToArchive = 0
foreach ($f in $docCandidates) { $bytesToArchive += [long]$f.Length }

# --------------------------------------------------------------------------- act
if ($Apply) {
    if ($docCandidates.Count -gt 0 -and -not (Test-Path -LiteralPath $ArchiveDir -PathType Container)) {
        try {
            New-Item -ItemType Directory -Path $ArchiveDir -Force | Out-Null
            Write-Log ('created archive directory: {0}' -f $ArchiveDir)
        } catch {
            $script:errors++
            Write-Log ('ERROR: cannot create archive dir {0}: {1}' -f $ArchiveDir, $_.Exception.Message) 'ERROR'
        }
    }

    foreach ($f in $deleteCandidates) {
        try {
            [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile(
                $f.FullName,
                [Microsoft.VisualBasic.FileIO.UIOption]::OnlyErrorDialogs,
                [Microsoft.VisualBasic.FileIO.RecycleOption]::SendToRecycleBin
            )
            if (Test-Path -LiteralPath $f.FullName) {
                throw 'file still exists after recycle-bin delete'
            }
            $deleted.Add($f)
            Write-Log ('[OK-RECYCLE] {0} | {1}' -f $f.Name, (Format-Bytes ([long]$f.Length)))
        } catch {
            $script:errors++
            Write-Log ('[ERROR-RECYCLE] {0}: {1}' -f $f.Name, $_.Exception.Message) 'ERROR'
        }
    }

    foreach ($f in $docCandidates) {
        try {
            if (-not (Test-Path -LiteralPath $ArchiveDir -PathType Container)) {
                throw ('archive directory is missing: {0}' -f $ArchiveDir)
            }
            $dest = Get-UniqueFilePath -Directory $ArchiveDir -Name $f.Name
            Move-Item -LiteralPath $f.FullName -Destination $dest -ErrorAction Stop
            $archived.Add([pscustomobject]@{ Source = $f.Name; Dest = $dest })
            Write-Log ('[OK-ARCHIVE] {0} -> {1}' -f $f.Name, $dest)
        } catch {
            $script:errors++
            Write-Log ('[ERROR-ARCHIVE] {0}: {1}' -f $f.Name, $_.Exception.Message) 'ERROR'
        }
    }
} else {
    foreach ($f in $deleteCandidates) {
        Write-Log ('[WOULD-RECYCLE] {0} | {1} | age {2:N1} d' -f $f.Name, (Format-Bytes ([long]$f.Length)), (Get-EffectiveAgeDays -File $f)) 'PLAN'
    }
    foreach ($f in $docCandidates) {
        Write-Log ('[WOULD-ARCHIVE] {0} | {1} -> {2}' -f $f.Name, (Format-Bytes ([long]$f.Length)), $ArchiveDir) 'PLAN'
    }
}

# --------------------------------------------------------------------------- summary
$sum = @(
    '',
    ('SUMMARY ({0})' -f $mode),
    ('  Recycle-bin candidates : {0} file(s), {1}' -f $deleteCandidates.Count, (Format-Bytes $bytesToDelete)),
    ('  Documents to archive   : {0} file(s), {1}  -> {2}' -f $docCandidates.Count, (Format-Bytes $bytesToArchive), $ArchiveDir),
    ('  Recent (skipped)       : {0} file(s) younger than the age threshold' -f $recentSkipped.Count),
    ('  Protected/attribute-skipped: {0} file(s)' -f $protectedSkipped.Count),
    ('  Manual review          : {0} file(s) left untouched' -f $reviewFiles.Count),
    ('  Directories (skipped)  : {0}' -f $dirSkipped.Count),
    ('  Errors                 : {0}' -f $script:errors)
)
if ($Apply) {
    $sum += ('  Actually recycled      : {0} file(s)' -f $deleted.Count)
    $sum += ('  Actually archived      : {0} file(s)' -f $archived.Count)
} else {
    $sum += '  Nothing was changed (DryRun). Use -Apply to execute.'
}
$sum += ('  Log: {0}' -f $logFile)

foreach ($line in $sum) { Write-Log $line }

if ($script:errors -gt 0) { exit 1 }
exit 0
