#Requires -RunAsAdministrator
# News Central: start at logon and keep this laptop collecting on AC power with the lid closed.
# Run from an elevated PowerShell:
#   powershell -ExecutionPolicy Bypass -File tools\windows\install.ps1
# Undo with tools\windows\uninstall.ps1.

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$dataDir = Join-Path $repo 'data'
$taskName = 'NewsCentral'
$utf8NoBom = New-Object System.Text.UTF8Encoding $false

$launcher = Get-Command pyw.exe -ErrorAction SilentlyContinue
if (-not $launcher) { $launcher = Get-Command pythonw.exe -ErrorAction SilentlyContinue }
if (-not $launcher) { throw 'Neither pyw.exe nor pythonw.exe is on PATH. Install Python from python.org first.' }

New-Item -ItemType Directory -Force $dataDir | Out-Null

# 1. config.json: only created when missing, never edited.
$configPath = Join-Path $repo 'config.json'
if (Test-Path $configPath) {
    Write-Host 'config.json already exists and was left unchanged.'
    Write-Host '  Make sure it has  "keep_awake": true  and  "log_file": "data/server.log".'
} else {
    $json = "{`n  `"keep_awake`": true,`n  `"log_file`": `"data/server.log`""
    if ($env:OneDrive) {
        $backupDir = (Join-Path $env:OneDrive 'NewsCentral-backup').Replace('\', '\\')
        $json += ",`n  `"backup_dir`": `"$backupDir`""
    }
    $json += "`n}`n"
    [System.IO.File]::WriteAllText($configPath, $json, $utf8NoBom)
    Write-Host 'Created config.json (keep_awake on, log in data\server.log).'
    if ($env:OneDrive) { Write-Host "  Daily backup goes to $(Join-Path $env:OneDrive 'NewsCentral-backup')." }
}

# 2. Power, AC only. Battery settings are not touched.
$powerSettings = @(
    @{ name = 'lid';       sub = 'SUB_BUTTONS'; id = 'LIDACTION' },
    @{ name = 'standby';   sub = 'SUB_SLEEP';   id = 'STANDBYIDLE' },
    @{ name = 'hibernate'; sub = 'SUB_SLEEP';   id = 'HIBERNATEIDLE' }
)

function Get-AcIndex($sub, $id) {
    # powercfg prints localized labels, so read the values by position: the last two
    # 0x numbers of the output are the current AC and DC indexes, in that order.
    # /qh also lists hidden settings: some plans hide LIDACTION, and /query then prints nothing for it.
    $out = (powercfg /qh SCHEME_CURRENT $sub $id) -join "`n"
    $found = [regex]::Matches($out, '0x[0-9a-fA-F]{8}')
    if ($found.Count -lt 2) { throw "Could not read power setting $sub $id" }
    return [Convert]::ToInt64($found[$found.Count - 2].Value, 16)
}

$backupPath = Join-Path $dataDir 'power-backup.json'
if (-not (Test-Path $backupPath)) {
    $backup = @{}
    foreach ($s in $powerSettings) { $backup[$s.name] = Get-AcIndex $s.sub $s.id }
    [System.IO.File]::WriteAllText($backupPath, ($backup | ConvertTo-Json), $utf8NoBom)
    Write-Host "Saved the previous AC power settings to $backupPath"
}
foreach ($s in $powerSettings) {
    powercfg /setacvalueindex SCHEME_CURRENT $s.sub $s.id 0
    if ($LASTEXITCODE -ne 0) { throw "powercfg could not set $($s.id)" }
}
powercfg /setactive SCHEME_CURRENT
Write-Host 'On AC power: closing the lid does nothing, no sleep, no hibernate.'

# 3. Scheduled task: start hidden at logon, restart on failure.
$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute $launcher.Source -Argument 'server.py' -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Force | Out-Null
Start-ScheduledTask -TaskName $taskName
Write-Host "Registered and started the scheduled task '$taskName' for $user."
Write-Host 'Reader: http://127.0.0.1:8787   Log: data\server.log'
