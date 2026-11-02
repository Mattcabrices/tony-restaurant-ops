# Optional, explicit owner action. Never invoked by setup, tests or the model.
$ErrorActionPreference = 'Stop'
$TonyRoot = $PSScriptRoot
$TonyUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$TonyScript = Join-Path $TonyRoot 'Start-Tony.ps1'
$TonyAction = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument ('-NoProfile -WindowStyle Hidden -File "' + $TonyScript + '"') -WorkingDirectory $TonyRoot
$TonyTrigger = New-ScheduledTaskTrigger -AtLogOn -User $TonyUser
$TonyPrincipal = New-ScheduledTaskPrincipal -UserId $TonyUser -LogonType Interactive -RunLevel Limited
$TonySettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName 'TonyRestaurantOps-LocalOperations' -Action $TonyAction -Trigger $TonyTrigger -Principal $TonyPrincipal -Settings $TonySettings -Description 'Local Tony restaurant assistant; optional schedules configured separately.'
Write-Host 'Startup installed for your signed-in Windows account. This does not enable briefing or research schedules.'
