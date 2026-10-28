# start-test.ps1
# Starts the KidsCrown backend (own window), forwards port 8000 to the phone,
# clears the old log, then tails the probe log and saves a copy to a file.
#
# Run:  powershell -ExecutionPolicy Bypass -File .\start-test.ps1

$Repo   = "C:\Users\ramne\IdeaProjects\kidscrown\kidscrown"
$Adb    = "$env:LOCALAPPDATA\Android\Sdk\platform-tools\adb.exe"
$Device = "10BD6D2PS2000R5"
$Health = "http://127.0.0.1:8000/health"

$LogDir = Join-Path $env:USERPROFILE "kidscrown-logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogFile = Join-Path $LogDir ("kidscrown-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".txt")

# 1. Is the phone connected?
& $Adb start-server | Out-Null
$devices = & $Adb devices
if (-not ($devices -match $Device)) {
    Write-Warning "Phone $Device not found. Plug in the USB cable, unlock the phone, accept the USB debugging prompt, then run this again."
    & $Adb devices
    exit 1
}

# 2. Is the backend already running? If not, start it in its own window.
function Test-Backend {
    try { Invoke-RestMethod $Health -TimeoutSec 2 | Out-Null; return $true } catch { return $false }
}

if (-not (Test-Backend)) {
    $cmd = "Set-Location '$Repo'; .\.venv\Scripts\Activate.ps1; uvicorn app.main:app --host 0.0.0.0 --port 8000"
    Start-Process powershell -ArgumentList @("-NoExit", "-ExecutionPolicy", "Bypass", "-Command", "`"$cmd`"")
    Write-Host "Starting backend..."
    for ($i = 0; $i -lt 40; $i++) {
        Start-Sleep -Seconds 1
        if (Test-Backend) { break }
    }
}
if (-not (Test-Backend)) {
    Write-Warning "Backend did not come up on port 8000. Look at the uvicorn window for the error."
    exit 1
}
Write-Host "Backend is up."

# 3. Forward the phone's port 8000 to this PC.
& $Adb -s $Device reverse tcp:8000 tcp:8000

# 4. Is the accessibility service enabled?
$enabled = & $Adb -s $Device shell settings get secure enabled_accessibility_services
if ($enabled -notmatch "com.kidscrown.probe") {
    Write-Warning "KidsCrown Probe is not enabled. On the phone: Settings > Accessibility > KidsCrown Probe > On."
} else {
    Write-Host "Accessibility service is listed as enabled. If nothing logs, switch it off and on once."
}

# 5. Clear the old log and tail the new one (also saved to a file).
& $Adb -s $Device logcat -c
Write-Host ""
Write-Host "Logging to: $LogFile"
Write-Host "Press Ctrl+C to stop. Emergency exit if an overlay sticks: .\stop-test.ps1"
Write-Host ""
& $Adb -s $Device logcat -s KidsCrown | Tee-Object -FilePath $LogFile
