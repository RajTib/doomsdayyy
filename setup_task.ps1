# Registers a daily Windows Task Scheduler job that runs the watcher.
#   .\setup_task.ps1              -> daily at 09:00
#   .\setup_task.ps1 -Time 18:30  -> daily at 18:30
# Remove with: Unregister-ScheduledTask -TaskName DoomsdayWatcher -Confirm:$false
param([string]$Time = "09:00")

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$pyw = Join-Path $root ".venv\Scripts\pythonw.exe"   # pythonw = no console window
if (-not (Test-Path $pyw)) { throw "Missing $pyw - create the venv first (see README)." }
if (-not (Test-Path (Join-Path $root ".env"))) { Write-Warning ".env not found - SMS will fail until you create it." }

$action   = New-ScheduledTaskAction -Execute $pyw -Argument "watcher.py" -WorkingDirectory $root
$trigger  = New-ScheduledTaskTrigger -Daily -At $Time
# StartWhenAvailable: if the PC was off at $Time, run as soon as it's back on.
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

# Runs as you, only while you're logged in (Chrome needs a desktop session).
Register-ScheduledTask -TaskName "DoomsdayWatcher" -Action $action -Trigger $trigger `
    -Settings $settings -Description "Checks BookMyShow for Avengers: Doomsday in Kanpur" -Force | Out-Null

Write-Host "Registered 'DoomsdayWatcher' to run daily at $Time. Test it now with:"
Write-Host "  Start-ScheduledTask -TaskName DoomsdayWatcher"
