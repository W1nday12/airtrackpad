$ErrorActionPreference = 'Stop'
$ruleName = 'AirTrackpad'

function Test-Admin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-Admin)) {
    Write-Host 'Требуются права администратора. Сейчас появится запрос UAC...'
    Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"' + $PSCommandPath + '"')
    )
    exit
}

Write-Host ''
Write-Host 'AirTrackpad: разрешаю входящие подключения (TCP 5050-5060)...'
netsh advfirewall firewall delete rule name=$ruleName | Out-Null
netsh advfirewall firewall add rule name=$ruleName dir=in action=allow protocol=TCP localport=5050-5060 profile=any | Out-Null

if ($LASTEXITCODE -eq 0) {
    Write-Host 'Готово. Правило добавлено для всех профилей сети (домен, частная, общедоступная).' -ForegroundColor Green
    Write-Host 'Теперь телефон сможет подключаться, даже когда Windows считает сеть общедоступной.'
} else {
    Write-Host 'Не удалось добавить правило. Запустите файл от имени администратора.' -ForegroundColor Red
}

Write-Host ''
Read-Host 'Нажмите Enter, чтобы закрыть'
