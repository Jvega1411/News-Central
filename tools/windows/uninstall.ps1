#Requires -RunAsAdministrator
# News Central: remove the scheduled task and restore the AC power settings saved by install.ps1.
#   powershell -ExecutionPolicy Bypass -File tools\windows\uninstall.ps1

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$taskName = 'NewsCentral'

$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($task) {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Host "Removed the scheduled task '$taskName'."
} else {
    Write-Host "No scheduled task '$taskName' found."
}

$backupPath = Join-Path (Join-Path $repo 'data') 'power-backup.json'
if (Test-Path $backupPath) {
    $backup = Get-Content -Raw $backupPath | ConvertFrom-Json
    powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION $backup.lid
    powercfg /setacvalueindex SCHEME_CURRENT SUB_SLEEP STANDBYIDLE $backup.standby
    powercfg /setacvalueindex SCHEME_CURRENT SUB_SLEEP HIBERNATEIDLE $backup.hibernate
    powercfg /setactive SCHEME_CURRENT
    Remove-Item $backupPath
    Write-Host 'Restored the previous AC power settings.'
} else {
    Write-Host 'No saved power settings found; power settings were left as they are.'
}
Write-Host 'config.json and data\ were left in place. A server that is still running keeps running until it is closed.'
