<#
.SYNOPSIS
    Local daily backup of the WebTrackpad project into a ZIP archive (no OneDrive).

.DESCRIPTION
    Creates <BackupDir>\WebTrackpad-YYYY-MM-DD_HHmm.zip from <ProjectPath> and keeps
    the newest -Keep archives (default 10).

    Exclusions: .venv, venv, .git, __pycache__, logs, .mypy_cache, .pytest_cache,
                .ruff_cache, node_modules, *.pyc, *.pyo, *.log, qr.png, Thumbs.db,
                desktop.ini, .DS_Store.

    Implementation notes:
      * Compress-Archive cannot exclude files, so the file list is built manually with
        Get-ChildItem -Recurse + filters.
      * Compress-Archive flattens a plain -Path file list (it would lose sub-folders),
        so the filtered files are first mirrored into a temporary staging folder
        (same relative paths) and then packed with -Path "<staging>\*". This keeps
        the archive structure identical to the project.
      * After packing the ZIP is opened for reading (System.IO.Compression.FileSystem):
        it must be readable, non-empty and contain server.py. A broken backup is
        deleted and the script exits with code 1, so it is not counted by rotation.
      * Rotation runs only after a verified archive was created.
      * Everything is logged to <LogDir>\backup-YYYYMMDD.log.

.PARAMETER ProjectPath
    Project folder to back up. Default: D:\Download\project\WebTrackpad.

.PARAMETER BackupDir
    Destination folder. Default: D:\Backups\WebTrackpad.

.PARAMETER LogDir
    Log folder. Default: D:\Download\project\logs.

.PARAMETER Keep
    Number of newest archives to keep. Default: 10.

.PARAMETER RetryCount
    Copy retries for temporarily locked files. Default: 3.

.EXAMPLE
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\backup.ps1

.NOTES
    Windows PowerShell 5.1 compatible. Requires no administrator rights.
#>
[CmdletBinding()]
param(
    [string]$ProjectPath = 'D:\Download\project\WebTrackpad',
    [string]$BackupDir = 'D:\Backups\WebTrackpad',
    [string]$LogDir = 'D:\Download\project\logs',
    [int]$Keep = 10,
    [int]$RetryCount = 3
)

$ErrorActionPreference = 'Continue'
$script:errors = 0
$stagingDir = $null
$exitCode = 0

# --------------------------------------------------------------------------- log
if (-not (Test-Path -LiteralPath $LogDir -PathType Container)) {
    try { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null } catch { }
}
$logFile = Join-Path $LogDir ('backup-' + (Get-Date -Format 'yyyyMMdd') + '.log')

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
        # ignore log errors
    }
}

function Format-Bytes {
    param([long]$Bytes)
    if ($Bytes -ge 1GB) { return ('{0:N2} GB' -f ($Bytes / 1GB)) }
    if ($Bytes -ge 1MB) { return ('{0:N2} MB' -f ($Bytes / 1MB)) }
    if ($Bytes -ge 1KB) { return ('{0:N2} KB' -f ($Bytes / 1KB)) }
    return ('{0} B' -f $Bytes)
}

# --------------------------------------------------------------------------- main
try {
    Write-Log ('=' * 78)
    Write-Log ('backup start | project={0} | backupDir={1} | keep={2}' -f $ProjectPath, $BackupDir, $Keep)

    if (-not (Test-Path -LiteralPath $ProjectPath -PathType Container)) {
        throw ('project folder not found: {0}' -f $ProjectPath)
    }
    if (-not (Test-Path -LiteralPath $BackupDir -PathType Container)) {
        New-Item -ItemType Directory -Path $BackupDir -Force | Out-Null
        Write-Log ('created backup dir: {0}' -f $BackupDir)
    }

    $projectItem = Get-Item -LiteralPath $ProjectPath -Force
    $projectFull = $projectItem.FullName.TrimEnd('\')
    $projectPrefix = $projectFull + '\'

    # ------------------------------------------------------------------ file list
    $excludedDirNames = @('.venv', 'venv', '.git', '__pycache__', 'logs', '.mypy_cache', '.pytest_cache', '.ruff_cache', 'node_modules')
    $excludedFileNames = @('qr.png', 'thumbs.db', 'desktop.ini', '.ds_store')
    $excludedFilePatterns = @('*.pyc', '*.pyo', '*.log', '*.tmp', '*.swp')

    $allFiles = @(Get-ChildItem -LiteralPath $projectFull -Recurse -File -Force -ErrorAction SilentlyContinue)
    $files = New-Object System.Collections.Generic.List[object]
    foreach ($f in $allFiles) {
        $rel = $f.FullName.Substring($projectPrefix.Length)
        $parts = $rel.Split('\')
        $skip = $false
        foreach ($part in $parts) {
            if ($excludedDirNames -contains $part.ToLowerInvariant()) { $skip = $true; break }
        }
        if ($skip) { continue }
        $leaf = $parts[$parts.Length - 1].ToLowerInvariant()
        if ($excludedFileNames -contains $leaf) { continue }
        foreach ($pattern in $excludedFilePatterns) {
            if ($leaf -like $pattern) { $skip = $true; break }
        }
        if ($skip) { continue }
        $files.Add($f)
    }

    if ($files.Count -eq 0) {
        throw ('no files to back up after exclusions in {0}' -f $projectFull)
    }

    $totalBytes = 0
    foreach ($f in $files) { $totalBytes += [long]$f.Length }
    Write-Log ('selected {0} file(s), {1}' -f $files.Count, (Format-Bytes $totalBytes))

    # ------------------------------------------------------------------ staging copy
    $stagingDir = Join-Path ([System.IO.Path]::GetTempPath()) ('WebTrackpad-backup-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $stagingDir -Force | Out-Null
    Write-Log ('staging dir: {0}' -f $stagingDir)

    foreach ($f in $files) {
        $rel = $f.FullName.Substring($projectPrefix.Length)
        $dest = Join-Path $stagingDir $rel
        $parent = Split-Path -LiteralPath $dest -Parent
        if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
            New-Item -ItemType Directory -Path $parent -Force | Out-Null
        }
        $copied = $false
        $lastError = $null
        for ($attempt = 1; $attempt -le $RetryCount; $attempt++) {
            try {
                Copy-Item -LiteralPath $f.FullName -Destination $dest -Force -ErrorAction Stop
                $copied = $true
                break
            } catch {
                $lastError = $_
                Start-Sleep -Milliseconds (300 * $attempt)
            }
        }
        if (-not $copied) {
            throw ('cannot stage file {0}: {1}' -f $f.FullName, $lastError.Exception.Message)
        }
    }
    Write-Log 'staging copy complete'

    # ------------------------------------------------------------------ compress
    $stamp = Get-Date -Format 'yyyy-MM-dd_HHmm'
    $zipName = 'WebTrackpad-' + $stamp + '.zip'
    $zipPath = Join-Path $BackupDir $zipName
    if (Test-Path -LiteralPath $zipPath) {
        # extremely unlikely, but never overwrite an existing archive
        $zipName = 'WebTrackpad-' + $stamp + '-' + (Get-Random -Maximum 9999) + '.zip'
        $zipPath = Join-Path $BackupDir $zipName
        Write-Log ('archive name collision, using {0}' -f $zipName) 'WARN'
    }

    $compressOk = $false
    for ($attempt = 1; $attempt -le 2; $attempt++) {
        try {
            Compress-Archive -Path (Join-Path $stagingDir '*') -DestinationPath $zipPath -CompressionLevel Optimal -Force -ErrorAction Stop
            $compressOk = $true
            break
        } catch {
            $compressError = $_.Exception.Message
            Write-Log ('Compress-Archive attempt {0} failed: {1}' -f $attempt, $compressError) 'WARN'
            Start-Sleep -Seconds 2
        }
    }
    if (-not $compressOk) {
        throw ('Compress-Archive failed for {0}: {1}' -f $zipPath, $compressError)
    }
    Write-Log ('archive created: {0} ({1})' -f $zipPath, (Format-Bytes ([long](Get-Item -LiteralPath $zipPath).Length)))

    # ------------------------------------------------------------------ verify
    Add-Type -AssemblyName System.IO.Compression.FileSystem -ErrorAction Stop
    $zipStream = [System.IO.Compression.ZipFile]::OpenRead($zipPath)
    try {
        $entryNames = @()
        foreach ($entry in $zipStream.Entries) {
            $entryNames += ($entry.FullName -replace '/', '\')
        }
        $entryCount = $entryNames.Count
    } finally {
        $zipStream.Dispose()
    }

    $hasServer = $false
    foreach ($name in $entryNames) {
        if ($name -eq 'server.py' -or $name -like '*\server.py') { $hasServer = $true; break }
    }

    if ($entryCount -ne $files.Count) {
        throw ('archive verification failed: expected {0} entries, found {1}' -f $files.Count, $entryCount)
    }
    if (-not $hasServer) {
        throw 'archive verification failed: server.py not found inside the archive'
    }
    Write-Log ('archive verified: {0} entries, readable, server.py present' -f $entryCount)

    # ------------------------------------------------------------------ rotation
    $archives = @(Get-ChildItem -LiteralPath $BackupDir -Filter 'WebTrackpad-*.zip' -File -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending)
    $removed = 0
    if ($archives.Count -gt $Keep) {
        foreach ($old in ($archives | Select-Object -Skip $Keep)) {
            try {
                Remove-Item -LiteralPath $old.FullName -Force -ErrorAction Stop
                $removed++
                Write-Log ('rotation: removed old archive {0} ({1})' -f $old.Name, (Format-Bytes ([long]$old.Length)))
            } catch {
                $script:errors++
                Write-Log ('rotation: cannot remove {0}: {1}' -f $old.Name, $_.Exception.Message) 'ERROR'
            }
        }
    }
    Write-Log ('rotation: keeping {0} newest archive(s), removed {1}, total now {2}' -f $Keep, $removed, ($archives.Count - $removed))

    Write-Log ('backup OK: {0} | files={1} source={2} | errors={3}' -f $zipPath, $files.Count, (Format-Bytes $totalBytes), $script:errors)
} catch {
    $script:errors++
    Write-Log ('backup FAILED: {0}' -f $_.Exception.Message) 'ERROR'
} finally {
    if ($stagingDir -and (Test-Path -LiteralPath $stagingDir)) {
        try {
            Remove-Item -LiteralPath $stagingDir -Recurse -Force -ErrorAction Stop
            Write-Log ('staging dir removed: {0}' -f $stagingDir)
        } catch {
            Write-Log ('warning: cannot remove staging dir {0}: {1}' -f $stagingDir, $_.Exception.Message) 'WARN'
        }
    }
}

if ($script:errors -gt 0) { exit 1 }
exit 0
