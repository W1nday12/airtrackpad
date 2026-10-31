# ---------------------------------------------------------------------------
# AirTrackpad auto-sync
#
# Keeps this folder in sync with GitHub: every few seconds it looks for local
# changes, commits them and pushes to the remote. It also retries the push
# after the PC was offline (anything already committed locally is pushed as
# soon as the network is back).
#
# Started hidden at logon by "AirTrackpad GitHub Sync" in the Startup folder.
# Run manually with:
#   powershell -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File sync-github.ps1
# ---------------------------------------------------------------------------

$ErrorActionPreference = 'Continue'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$Repo = $PSScriptRoot
$Remote = 'origin'
$Branch = 'main'
$PollSeconds = 5
$DebounceSeconds = 3
$LogFile = Join-Path $Repo '.git\sync.log'

function Write-Log([string]$Message) {
    try {
        $line = '{0}  {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
        Add-Content -Path $LogFile -Value $line -Encoding UTF8
        if ((Get-Item $LogFile -ErrorAction SilentlyContinue).Length -gt 512KB) {
            $tail = Get-Content -Path $LogFile -ErrorAction SilentlyContinue | Select-Object -Last 800
            Set-Content -Path $LogFile -Value $tail -Encoding UTF8
        }
    } catch { }
}

# Prefer git from PATH; fall back to the portable copy bundled with this setup.
$Git = (Get-Command git.exe -ErrorAction SilentlyContinue).Source
if (-not $Git) { $Git = Join-Path $env:USERPROFILE 'Tools\PortableGit\cmd\git.exe' }
if (-not (Test-Path $Git)) {
    Write-Log "git not found, auto-sync disabled"
    exit 1
}
$Gh = (Get-Command gh.exe -ErrorAction SilentlyContinue).Source
if (-not $Gh) { $Gh = Join-Path $env:USERPROFILE 'Tools\gh\gh.exe' }
# The credential helper is `gh auth git-credential`; make sure git can find gh.
$GhDir = Split-Path -Parent $Gh
if ($GhDir -and (Test-Path $GhDir) -and ($env:PATH -notlike "*$GhDir*")) {
    $env:PATH = "$GhDir;$env:PATH"
}

Set-Location $Repo
Write-Log "auto-sync started (repo=$Repo, branch=$Branch)"

function Invoke-Git([string[]]$Arguments) {
    $out = & $Git @Arguments 2>&1
    return @{ Code = $LASTEXITCODE; Output = @($out) }
}

while ($true) {
    try {
        $status = (Invoke-Git @('status', '--porcelain')).Output
        if ($status.Count -gt 0 -and ($status -join '').Trim().Length -gt 0) {
            # Let the editor finish writing before we snapshot the files.
            Start-Sleep -Seconds $DebounceSeconds
            $status = (Invoke-Git @('status', '--porcelain')).Output
            if ($status.Count -gt 0 -and ($status -join '').Trim().Length -gt 0) {
                $names = @($status | ForEach-Object { ($_ -replace '^...', '').Trim() } | Where-Object { $_ })
                $short = $names | Select-Object -First 6
                $summary = ($short -join ', ')
                if ($names.Count -gt 6) { $summary = $summary + ' +' + ($names.Count - 6) + ' more' }
                $message = 'auto: ' + $summary
                if ($message.Length -gt 180) { $message = $message.Substring(0, 177) + '...' }
                Invoke-Git @('add', '-A') | Out-Null
                $commit = Invoke-Git @('commit', '-m', $message)
                if ($commit.Code -eq 0) {
                    Write-Log "committed: $message"
                } else {
                    Write-Log ("commit failed: " + (($commit.Output) -join ' '))
                }
            }
        }

        # Push anything that exists only locally (also retries after offline).
        $unpushed = (Invoke-Git @('log', '--oneline', "$Remote/$Branch..$Branch")).Output
        if ($unpushed.Count -gt 0 -and ($unpushed -join '').Trim().Length -gt 0) {
            Invoke-Git @('fetch', '--quiet', $Remote) | Out-Null
            $behind = (Invoke-Git @('log', '--oneline', "$Branch..$Remote/$Branch")).Output
            if ($behind.Count -gt 0 -and ($behind -join '').Trim().Length -gt 0) {
                $rebase = Invoke-Git @('pull', '--rebase', '--autostash', '--quiet', $Remote, $Branch)
                if ($rebase.Code -ne 0) {
                    Write-Log ("rebase failed: " + (($rebase.Output) -join ' '))
                    Invoke-Git @('rebase', '--abort') | Out-Null
                } else {
                    Write-Log 'rebased onto remote changes'
                }
            }
            $push = Invoke-Git @('push', '--quiet', $Remote, $Branch)
            if ($push.Code -eq 0) {
                Write-Log 'pushed to GitHub'
            } else {
                Write-Log ("push failed: " + (($push.Output) -join ' '))
            }
        }
    } catch {
        Write-Log ("sync error: " + $_.Exception.Message)
    }
    Start-Sleep -Seconds $PollSeconds
}
