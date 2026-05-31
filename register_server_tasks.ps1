# Register Task Scheduler entries on the user's own server:
#   - DomainWatch:      runs check.py every 1 minute
#   - BackorderAPI:     runs uvicorn for backorder_api at boot
#   - CloudflaredTunnel: runs cloudflared named tunnel at boot
#
# All run via S4U logon (no stored password) as the current user by default.
# Idempotent: unregisters before registering.
#
# Discover the values to pass FIRST (on the server):
#   (Get-Command python).Source            -> -PyExe
#   whoami /user                           -> -AdminSid (the SID column)
# Example:
#   .\register_server_tasks.ps1 -PyExe "C:\Users\User\AppData\Local\Programs\Python\Python312\python.exe" -DwDir "C:\domain-watch"

param(
    [string]$PyExe    = "C:\Users\User\AppData\Local\Programs\Python\Python312\python.exe",
    [string]$CfExe    = "C:\Program Files (x86)\cloudflared\cloudflared.exe",
    [string]$CfConfig = "$env:USERPROFILE\.cloudflared\config.yml",
    [string]$DwDir    = "C:\domain-watch",
    [string]$TunnelName = "domain-watch-backorder",
    [string]$AdminSid
)

$ErrorActionPreference = "Stop"

if (-not $AdminSid) {
    $AdminSid = ([System.Security.Principal.WindowsIdentity]::GetCurrent()).User.Value
    Write-Host "Using current user SID: $AdminSid"
}

$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -RestartCount 3 `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

$principal = New-ScheduledTaskPrincipal -UserId $AdminSid -LogonType S4U -RunLevel Highest

function Register-DwTask {
    param([string]$Name, [string]$Exe, [string]$ArgList, [string]$WorkingDir, $Trigger)
    Unregister-ScheduledTask -TaskName $Name -Confirm:$false -ErrorAction SilentlyContinue
    $action = New-ScheduledTaskAction -Execute $Exe -Argument $ArgList -WorkingDirectory $WorkingDir
    Register-ScheduledTask -TaskName $Name -Action $action -Trigger $Trigger -Principal $principal -Settings $settings | Out-Null
    Write-Host "Registered: $Name"
}

$atStartup = New-ScheduledTaskTrigger -AtStartup
$everyMinute = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes 1) `
    -RepetitionDuration (New-TimeSpan -Days 3650)

Register-DwTask -Name "DomainWatch" -Exe $PyExe -ArgList "check.py" -WorkingDir $DwDir -Trigger $everyMinute
Register-DwTask -Name "CloudflaredTunnel" -Exe $CfExe -ArgList "--config `"$CfConfig`" tunnel run $TunnelName" -WorkingDir "C:\" -Trigger $atStartup
Register-DwTask -Name "BackorderAPI" -Exe $PyExe -ArgList "-m uvicorn backorder_api:app --port 8000" -WorkingDir $DwDir -Trigger $atStartup

Write-Host ""
Write-Host "Starting boot tasks..."
Start-ScheduledTask -TaskName "CloudflaredTunnel"
Start-ScheduledTask -TaskName "BackorderAPI"
Start-ScheduledTask -TaskName "DomainWatch"
Start-Sleep -Seconds 8

Write-Host ""
Write-Host "Status:"
Get-ScheduledTask -TaskName "DomainWatch", "CloudflaredTunnel", "BackorderAPI" |
    ForEach-Object {
        $info = $_ | Get-ScheduledTaskInfo
        "{0,-18} state={1,-9} lastRun={2} lastResult={3}" -f $_.TaskName, $_.State, $info.LastRunTime, $info.LastTaskResult
    }
