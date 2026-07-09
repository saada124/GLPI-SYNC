$taskName = 'GLPI-AppSheet-Sync'
# 1. Standardized server path
$scriptPath = 'C:\Scripts\GLPI-SYNC\run_sync.bat' 

$action = New-ScheduledTaskAction -Execute 'C:\Windows\System32\cmd.exe' -Argument "/c `"$scriptPath`""

# Trigger remains the same (runs every 10 mins indefinitely)
$trigger = New-ScheduledTaskTrigger -RepetitionInterval (New-TimeSpan -Minutes 10) -RepetitionDuration (New-TimeSpan -Days 3650) -At (Get-Date).AddMinutes(1) -Once

# 2. Switched to SYSTEM account to ensure it runs 24/7 without user login dependencies
$principal = New-ScheduledTaskPrincipal -UserId 'NT AUTHORITY\SYSTEM' -RunLevel Highest

try {
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Force -ErrorAction Stop
    Write-Output "Task '$taskName' created successfully on the server."
    Get-ScheduledTask -TaskName $taskName | Format-List TaskName, State, Triggers
}
catch {
    Write-Error "Failed to register task: $_"
}