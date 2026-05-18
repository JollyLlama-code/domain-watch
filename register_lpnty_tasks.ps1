# Register Task Scheduler entries on LPNTY:
#   - CloudflaredTunnel: runs cloudflared with the named tunnel at boot
#   - BackorderAPI: runs uvicorn for backorder_api at boot
#
# Both run as built-in Administrator (Rendszergazda) via S4U logon so they
# don't need a stored password, same pattern as the existing DomainWatch task.
#
# Idempotent: unregisters before registering.

$ErrorActionPreference = "Stop"

$adminSid = "S-1-5-21-3672051506-881372973-3623165799-500"
$pyExe = "C:\Users\Rendszergazda\AppData\Local\Programs\Python\Python312\python.exe"
$cfExe = "C:\Program Files (x86)\cloudflared\cloudflared.exe"
$dwDir = "C:\domain-watch"
$cfConfig = "C:\Users\Rendszergazda\.cloudflared\config.yml"

function Register-Bg-Task {
    param(
        [string]$Name,
        [string]$Exe,
        [string]$ArgList,
        [string]$WorkingDir
    )
    Unregister-ScheduledTask -TaskName $Name -Confirm:$false -ErrorAction SilentlyContinue
    $action = New-ScheduledTaskAction -Execute $Exe -Argument $ArgList -WorkingDirectory $WorkingDir
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal -UserId $adminSid -LogonType S4U -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet `
        -MultipleInstances IgnoreNew `
        -RestartInterval (New-TimeSpan -Minutes 1) `
        -RestartCount 3 `
        -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName $Name -Action $action -Trigger $trigger -Principal $principal -Settings $settings | Out-Null
    Write-Host "Registered: $Name"
}

Register-Bg-Task `
    -Name "CloudflaredTunnel" `
    -Exe $cfExe `
    -ArgList"--config $cfConfig tunnel run domain-watch-backorder" `
    -WorkingDir "C:\"

Register-Bg-Task `
    -Name "BackorderAPI" `
    -Exe $pyExe `
    -ArgList"-m uvicorn backorder_api:app --port 8000" `
    -WorkingDir $dwDir

Write-Host ""
Write-Host "Starting tasks..."
Start-ScheduledTask -TaskName "CloudflaredTunnel"
Start-ScheduledTask -TaskName "BackorderAPI"
Start-Sleep -Seconds 8

Write-Host ""
Write-Host "Status:"
Get-ScheduledTask -TaskName "CloudflaredTunnel", "BackorderAPI" |
    ForEach-Object {
        $info = $_ | Get-ScheduledTaskInfo
        "{0,-22} state={1,-9} lastRun={2} lastResult={3}" -f $_.TaskName, $_.State, $info.LastRunTime, $info.LastTaskResult
    }
