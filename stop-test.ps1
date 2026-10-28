# stop-test.ps1
# Emergency exit and cleanup. Removes a stuck overlay (force-stops the probe app),
# removes the port forward, and stops whatever is listening on port 8000.
#
# Run:  powershell -ExecutionPolicy Bypass -File .\stop-test.ps1

$Adb    = "$env:LOCALAPPDATA\Android\Sdk\platform-tools\adb.exe"
$Device = "10BD6D2PS2000R5"

& $Adb -s $Device shell am force-stop com.kidscrown.probe
Write-Host "Probe app stopped (overlay removed)."

& $Adb -s $Device reverse --remove tcp:8000 2>$null
Write-Host "Port forward removed."

$listeners = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
foreach ($l in $listeners) {
    Stop-Process -Id $l.OwningProcess -Force -ErrorAction SilentlyContinue
    Write-Host "Stopped backend process $($l.OwningProcess)."
}
Write-Host "Done. To use the phone again, turn the accessibility service off and on."
